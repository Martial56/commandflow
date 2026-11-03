"""Services de la logistique : toute écriture des tournées, des chargements et des
livraisons passe ici.

Les vues appellent ces services ; les services vérifient les droits de l'utilisateur
courant et appellent les transitions (§5 et §6 de modele-donnees.md, décisions 8.3 et
8.4). La tournée est verrouillée (`select_for_update`) pendant chaque modification de ses
chargements et de ses livraisons.
"""

from decimal import Decimal, InvalidOperation

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.db import models, transaction
from django.db.models import Max
from django.utils import timezone
from django_fsm import can_proceed

from commandes.models import Commande, Statut
from comptes.models import Role
from entreprises.parametres import creneaux
from noyau.contexte import AutreEntreprise, entreprise_obligatoire, utilisateur_courant
from noyau.models import PieceJointe, Validation
from noyau.services import (
    decider_validation,
    demander_validation,
    joindre_fichier,
    prochain_numero,
)

from .etat import exceptions, preuves_manquantes
from .models import (
    AccordClient,
    Chargement,
    DecisionRetour,
    EtatRetour,
    Incident,
    LigneChargement,
    LigneLivraison,
    Livraison,
    MotifEcart,
    MotifEchec,
    MoyenAccord,
    ObjetAccord,
    Preuve,
    Reliquat,
    Retour,
    StatutChargement,
    StatutLivraison,
    StatutTournee,
    Tournee,
    TypeIncident,
    TypePreuve,
)
from .rules import livreur_affecte

# Tournée modifiable : pas encore partie (décision 8.3 : une tournée partie est figée).
STATUTS_MODIFIABLES = [
    StatutTournee.PLANIFIEE,
    StatutTournee.EN_CHARGEMENT,
    StatutTournee.DEPART_AUTORISE,
]
# Chargement encore dans sa tournée (tout sauf « Retiré »).
STATUTS_CHARGEMENT_ACTIFS = [s for s in StatutChargement.values if s != StatutChargement.RETIRE]


class ActionImpossible(Exception):
    """Action refusée par une règle métier ; le message s'affiche tel quel."""


# --- Outils -------------------------------------------------------------------


def _utilisateur():
    utilisateur = utilisateur_courant()
    if utilisateur is None:
        raise PermissionDenied("Action à attribuer à un utilisateur connecté.")
    return utilisateur


def _exiger(permission, objet=None):
    if not _utilisateur().has_perm(permission, objet):
        raise PermissionDenied("Votre rôle ne permet pas cette action.")


def _meme_entreprise(*objets):
    entreprise = entreprise_obligatoire()
    for objet in objets:
        if objet is not None and objet.entreprise_id != entreprise.pk:
            raise AutreEntreprise(f"{objet} n'appartient pas à l'entreprise courante.")


def _verrouiller(tournee):
    return Tournee.objects.select_for_update().get(pk=tournee.pk)


def _exiger_modifiable(tournee):
    if tournee.statut not in STATUTS_MODIFIABLES:
        raise ActionImpossible("Tournée partie : elle ne peut plus être modifiée.")


def _verifier_livreur(chauffeur, prestataire, vehicule):
    """Un chauffeur interne ou un prestataire, actif et de l'entreprise (§3.5)."""
    if (chauffeur is None) == (prestataire is None):
        raise ActionImpossible("Choisir un chauffeur ou un prestataire.")
    _meme_entreprise(chauffeur, prestataire, vehicule)
    if chauffeur is not None and not (
        chauffeur.is_active and chauffeur.groups.filter(name=Role.CHAUFFEUR.label).exists()
    ):
        raise ActionImpossible(f"{chauffeur} n'est pas un chauffeur actif.")
    if prestataire is not None and not prestataire.actif:
        raise ActionImpossible(f"Le prestataire {prestataire} est désactivé.")
    if vehicule is not None and not vehicule.actif:
        raise ActionImpossible(f"Le véhicule {vehicule} est désactivé.")


def _numeroter(tournee, chargements):
    """Ordre de passage 1, 2, 3… dans l'ordre donné."""
    for ordre, chargement in enumerate(chargements, start=1):
        if chargement.ordre_passage != ordre:
            chargement.ordre_passage = ordre
            chargement.save()


def actualiser_tournee(tournee):
    """Statut de la tournée selon ses chargements (actions du système, §5).

    Départ autorisé dès que chaque chargement actif peut partir ; retour « en
    chargement » sinon (commande ajoutée, réception contestée).
    """
    if tournee.statut == StatutTournee.EN_CHARGEMENT and can_proceed(tournee.autoriser_depart):
        tournee.autoriser_depart()
        tournee.save()
    elif tournee.statut == StatutTournee.DEPART_AUTORISE and not tournee.tout_peut_partir():
        tournee.rouvrir_chargement()
        tournee.save()
    return tournee


# --- Tournées -----------------------------------------------------------------


@transaction.atomic
def creer_tournee(*, date, chauffeur=None, prestataire=None, vehicule=None):
    """Nouvelle tournée planifiée, numérotée `TRN-AAAA-000123` (Gestionnaire, Direction)."""
    _exiger("logistique.gerer_tournees")
    _verifier_livreur(chauffeur, prestataire, vehicule)
    return Tournee.objects.create(
        numero=prochain_numero("TRN", date_reference=date),
        date=date,
        chauffeur=chauffeur,
        prestataire=prestataire,
        vehicule=vehicule,
    )


@transaction.atomic
def changer_livreur(tournee, *, chauffeur=None, prestataire=None, vehicule=None):
    """Change le chauffeur (ou le prestataire) et le véhicule avant le départ.

    Refusé si un chargement est déjà reçu : la réception engage le livreur qui l'a
    confirmée ; retirer puis réaffecter la commande.
    """
    tournee = _verrouiller(tournee)
    _exiger("logistique.gerer_tournee", tournee)
    _exiger_modifiable(tournee)
    _verifier_livreur(chauffeur, prestataire, vehicule)
    if tournee.chargements.filter(statut=StatutChargement.RECUE_CHAUFFEUR).exists():
        raise ActionImpossible(
            "Des commandes sont déjà reçues par le livreur : les retirer avant de le changer."
        )
    tournee.chauffeur = chauffeur
    tournee.prestataire = prestataire
    tournee.vehicule = vehicule
    tournee.save()
    return tournee


# --- Affectation des commandes -------------------------------------------------


def commandes_affectables(date):
    """Commandes « Prête au chargement » de la date, sans chargement actif (8.3)."""
    return (
        Commande.objects.filter(statut=Statut.PRETE_CHARGEMENT, date_livraison=date)
        .exclude(chargements__statut__in=STATUTS_CHARGEMENT_ACTIFS)
        .select_related("client", "adresse")
        .order_by("creneau", "adresse__commune", "numero")
    )


@transaction.atomic
def affecter_commande(tournee, commande):
    """Ajoute une commande à la tournée : un chargement, une ligne par produit."""
    tournee = _verrouiller(tournee)
    _exiger("logistique.gerer_tournee", tournee)
    _exiger_modifiable(tournee)
    commande = Commande.objects.select_for_update().get(pk=commande.pk)
    if commande.statut != Statut.PRETE_CHARGEMENT:
        raise ActionImpossible(
            f"{commande} n'est pas prête au chargement ({commande.get_statut_display()})."
        )
    if commande.date_livraison != tournee.date:
        raise ActionImpossible(f"{commande} n'est pas à livrer le jour de la tournée.")
    if commande.chargements.filter(statut__in=STATUTS_CHARGEMENT_ACTIFS).exists():
        raise ActionImpossible(f"{commande} est déjà affectée à une tournée.")

    dernier = tournee.chargements_actifs().aggregate(Max("ordre_passage"))["ordre_passage__max"]
    chargement = Chargement.objects.create(
        tournee=tournee, commande=commande, ordre_passage=(dernier or 0) + 1
    )
    for ligne in commande.lignes_actives.order_by("pk"):
        LigneChargement.objects.create(
            chargement=chargement, ligne_commande=ligne, quantite_attendue=ligne.quantite
        )
    actualiser_tournee(tournee)
    return chargement


@transaction.atomic
def retirer_commande(chargement):
    """Retire une commande avant le départ ; elle redevient affectable (8.3)."""
    tournee = _verrouiller(chargement.tournee)
    chargement = Chargement.objects.select_for_update().get(pk=chargement.pk)
    if not can_proceed(chargement.retirer, check_conditions=False):
        raise ActionImpossible("Commande déjà retirée de la tournée.")
    if not chargement.retirer._django_fsm.has_transition_perm(
        chargement, chargement.statut, _utilisateur()
    ):
        raise PermissionDenied("Votre rôle ne permet pas cette action.")
    if not can_proceed(chargement.retirer):
        raise ActionImpossible("Tournée partie : elle ne peut plus être modifiée.")
    chargement.retirer()
    chargement.save()
    _numeroter(tournee, tournee.chargements_actifs().order_by("ordre_passage", "pk"))
    actualiser_tournee(tournee)
    return chargement


# --- Ordre de passage ----------------------------------------------------------------


@transaction.atomic
def reordonner(tournee, chargements):
    """Fixe l'ordre de passage : `chargements` contient tous les chargements actifs.

    Tournée en cours : seuls les arrêts restants changent de place ; un arrêt déjà
    traité garde son rang (décision 8.4).
    """
    tournee = _verrouiller(tournee)
    _exiger("logistique.gerer_tournee", tournee)
    if tournee.statut != StatutTournee.EN_COURS:
        _exiger_modifiable(tournee)
    actuel = list(tournee.chargements_actifs().order_by("ordre_passage", "pk"))
    actifs = {chargement.pk: chargement for chargement in actuel}
    ordre = [getattr(chargement, "pk", chargement) for chargement in chargements]
    if sorted(ordre) != sorted(actifs):
        raise ActionImpossible("L'ordre doit reprendre toutes les commandes de la tournée.")
    if tournee.statut == StatutTournee.EN_COURS:
        traites = arrets_traites(tournee)
        if any(c.pk in traites and c.pk != pk for c, pk in zip(actuel, ordre, strict=True)):
            raise ActionImpossible("Un arrêt déjà traité garde sa place.")
    _numeroter(tournee, [actifs[pk] for pk in ordre])
    return tournee


def arrets_traites(tournee):
    """Chargements dont la livraison est traitée (livrée, partielle, échouée, reportée)."""
    return set(
        Livraison.objects.filter(
            chargement__tournee=tournee, statut__in=STATUTS_LIVRAISON_TRAITEE
        ).values_list("chargement_id", flat=True)
    )


@transaction.atomic
def proposer_ordre(tournee):
    """Ordre proposé : par créneau, puis par commune (8.3) ; le gestionnaire ajuste."""
    tournee = _verrouiller(tournee)
    _exiger("logistique.gerer_tournee", tournee)
    _exiger_modifiable(tournee)
    rangs = {creneau: rang for rang, creneau in enumerate(creneaux(tournee.entreprise))}
    chargements = sorted(
        tournee.chargements_actifs().select_related("commande__adresse"),
        key=lambda c: (
            rangs.get(c.commande.creneau, len(rangs)),
            c.commande.creneau,
            c.commande.adresse.commune.casefold(),
            c.commande.numero,
        ),
    )
    _numeroter(tournee, chargements)
    return chargements


# --- Contrôle et réception (§10, décisions 8.3) ------------------------------------------


def _verrouiller_chargement(chargement):
    """Verrouille la tournée puis le chargement (même ordre partout)."""
    _verrouiller(chargement.tournee)
    return Chargement.objects.select_for_update().select_related("tournee").get(pk=chargement.pk)


def _autoriser(objet, nom):
    """Vérifie le statut et le rôle (sans les conditions) de la transition `nom`."""
    methode = getattr(objet, nom)
    if not can_proceed(methode, check_conditions=False):
        raise ActionImpossible(f"Impossible depuis le statut « {objet.get_statut_display()} ».")
    # Rôle seul (has_transition_perm de django-fsm vérifierait aussi les conditions).
    if not methode._django_fsm.has_transition_perm(objet, objet.statut, _utilisateur()):
        raise PermissionDenied("Votre rôle ne permet pas cette action.")
    return methode


def _transition(objet, nom, message_conditions):
    """Vérifie statut, rôle et conditions de la transition `nom`, avec des messages clairs."""
    methode = _autoriser(objet, nom)
    if not can_proceed(methode):
        raise ActionImpossible(message_conditions)
    return methode


def _quantite(valeur):
    try:
        quantite = Decimal(str(valeur))
    except (InvalidOperation, TypeError, ValueError):
        raise ActionImpossible("Quantité invalide.") from None
    if not quantite.is_finite() or quantite < 0:
        raise ActionImpossible("Quantité invalide.")
    return quantite


def _saisir_recues(chargement, quantites):
    """Quantités reçues par le livreur : une par ligne du chargement ({ligne: quantité})."""
    quantites = {getattr(ligne, "pk", ligne): valeur for ligne, valeur in quantites.items()}
    lignes = list(chargement.lignes.all())
    if sorted(quantites) != sorted(ligne.pk for ligne in lignes):
        raise ActionImpossible("Indiquer la quantité reçue pour chaque produit.")
    for ligne in lignes:
        ligne.quantite_recue_chauffeur = _quantite(quantites[ligne.pk])
        ligne.save()
    return lignes


def _demander_exception(chargement, objet, motif, lignes=None, **contenu):
    if exceptions(chargement, objet, Validation.Decision.EN_ATTENTE).exists():
        raise ActionImpossible("Une demande est déjà en attente de décision.")
    contenu = {"objet": objet, **contenu}
    if lignes is not None:
        contenu["lignes"] = {str(pk): str(quantite) for pk, quantite in lignes.items()}
    return demander_validation(chargement, Validation.Action.EXCEPTION, motif, contenu)


@transaction.atomic
def compter_ligne(ligne, quantite, motif="", detail=""):
    """Le préparateur compte un produit chargé (maquette 3).

    Le premier comptage met le chargement « en vérification » et la tournée « en
    chargement ». Une ligne conforme perd son motif d'écart.
    """
    chargement = _verrouiller_chargement(ligne.chargement)
    tournee = chargement.tournee
    if chargement.statut == StatutChargement.A_PREPARER:
        _transition(chargement, "commencer_verification", "Tournée partie.")()
        chargement.save()
        if tournee.statut == StatutTournee.PLANIFIEE:
            tournee.commencer_chargement()
            tournee.save()
    elif chargement.statut != StatutChargement.EN_VERIFICATION:
        raise ActionImpossible(
            f"Contrôle impossible depuis le statut « {chargement.get_statut_display()} »."
        )
    else:
        _exiger("logistique.controler_chargement", chargement)

    ligne = chargement.lignes.get(pk=ligne.pk)
    ligne.quantite_preparee = _quantite(quantite)
    if ligne.conforme:
        ligne.motif_ecart, ligne.detail_ecart = "", ""
    else:
        if motif and motif not in MotifEcart.values:
            raise ActionImpossible("Motif d'écart inconnu.")
        ligne.motif_ecart, ligne.detail_ecart = motif, (detail or "").strip()
    ligne.save()
    return ligne


@transaction.atomic
def valider_preparation(chargement):
    """Toutes les lignes comptées et conformes : « Vérifiée par la préparation »."""
    chargement = _verrouiller_chargement(chargement)
    _transition(
        chargement,
        "valider_preparation",
        "Compter chaque produit ; en cas d'écart, le signaler au gestionnaire.",
    )(par=_utilisateur())
    chargement.save()
    return chargement


@transaction.atomic
def signaler_ecart(chargement, commentaire=""):
    """Écart constaté par le préparateur : décision d'un responsable demandée (§10)."""
    chargement = _verrouiller_chargement(chargement)
    lignes = list(chargement.lignes.all())
    if any(ligne.motif_ecart == MotifEcart.AUTRE and not ligne.detail_ecart for ligne in lignes):
        raise ActionImpossible("Préciser le motif « Autre ».")
    _transition(
        chargement,
        "constater_ecart",
        "Compter chaque produit et indiquer le motif de chaque écart.",
    )(par=_utilisateur())
    chargement.save()
    _demander_exception(
        chargement,
        "ecart",
        (commentaire or "").strip() or "Écart au chargement",
        lignes={ligne.pk: ligne.quantite_preparee for ligne in lignes},
        origine="preparation",
    )
    return chargement


@transaction.atomic
def completer_chargement(chargement):
    """Marchandise complétée après un écart : nouveau contrôle par le préparateur."""
    chargement = _verrouiller_chargement(chargement)
    _transition(chargement, "completer", "Tournée partie.")()
    chargement.save()
    return chargement


@transaction.atomic
def confirmer_reception(chargement, quantites):
    """Le livreur affecté confirme lui-même la réception (§10, maquette 4).

    Pour un prestataire sans compte, le gestionnaire saisit à sa place : une demande
    est créée, et la réception n'est confirmée qu'à l'acceptation par un autre
    responsable (décision 8.3).
    """
    chargement = _verrouiller_chargement(chargement)
    _autoriser(chargement, "confirmer_reception")
    _saisir_recues(chargement, quantites)
    if not can_proceed(chargement.confirmer_reception):
        raise ActionImpossible("Quantités différentes du chargement : signaler un écart.")
    utilisateur = _utilisateur()
    if not livreur_affecte(utilisateur, chargement):
        _demander_exception(
            chargement,
            "reception",
            f"Réception saisie pour le prestataire {chargement.tournee.livreur}",
            lignes={ligne.pk: ligne.quantite_recue_chauffeur for ligne in chargement.lignes.all()},
        )
        return chargement
    chargement.confirmer_reception(par=utilisateur)
    chargement.save()
    actualiser_tournee(chargement.tournee)
    return chargement


@transaction.atomic
def contester_reception(chargement, quantites, motif):
    """Le livreur conteste une quantité : l'écart est consigné avant le départ et soumis
    à un responsable (§10) ; la marchandise peut aussi être complétée."""
    chargement = _verrouiller_chargement(chargement)
    _autoriser(chargement, "contester")
    lignes = _saisir_recues(chargement, quantites)
    chargement.motif_contestation = (motif or "").strip()
    _transition(
        chargement,
        "contester",
        "Indiquer le motif et la quantité réellement reçue.",
    )()
    chargement.save()
    _demander_exception(
        chargement,
        "ecart",
        chargement.motif_contestation,
        lignes={ligne.pk: ligne.quantite_recue_chauffeur for ligne in lignes},
        origine="livreur",
    )
    actualiser_tournee(chargement.tournee)
    return chargement


@transaction.atomic
def demander_exception_depart(chargement, motif):
    """Faire partir la tournée sans la réception de cette commande (décision 8.3).

    Demandée par le Gestionnaire, décidée par un autre responsable.
    """
    chargement = _verrouiller_chargement(chargement)
    _exiger("logistique.demander_exception_depart", chargement)
    _exiger_modifiable(chargement.tournee)
    if chargement.statut in (StatutChargement.RECUE_CHAUFFEUR, StatutChargement.RETIRE):
        raise ActionImpossible(
            f"Aucune exception utile au statut « {chargement.get_statut_display()} »."
        )
    if not (motif or "").strip():
        raise ActionImpossible("Indiquer le motif de l'exception.")
    return _demander_exception(chargement, "depart", motif.strip())


@transaction.atomic
def decider_exception_chargement(validation, *, accepter):
    """Décision d'un autre responsable (Direction, Gestionnaire) sur une demande liée
    à un chargement. Acceptée : écart retenu, départ autorisé ou réception confirmée.
    Tout est annulé si l'effet ne peut plus s'appliquer (demande dépassée)."""
    chargement = _verrouiller_chargement(validation.objet)
    _exiger("logistique.valider_exception", chargement)
    validation = decider_validation(validation, accepter=accepter)
    if not accepter:
        return validation

    objet = validation.contenu.get("objet")
    if objet == "ecart":
        # Seule la dernière demande d'écart s'applique (le chargement a pu être recompté).
        derniere = exceptions(chargement, "ecart", *Validation.Decision.values).latest("pk")
        if chargement.statut != StatutChargement.ECART_CONSTATE or derniere.pk != validation.pk:
            raise ActionImpossible("Demande dépassée : le chargement a changé depuis ; la refuser.")
        chargement.accepter_exception()
        chargement.save()
    elif objet == "depart":
        _exiger_modifiable(chargement.tournee)
    elif objet == "reception":
        recues = {int(pk): quantite for pk, quantite in validation.contenu["lignes"].items()}
        if chargement.statut != StatutChargement.VERIFIEE_PREPARATION:
            raise ActionImpossible("Demande dépassée : le chargement a changé depuis ; la refuser.")
        _saisir_recues(chargement, recues)
        if not can_proceed(chargement.confirmer_reception):
            raise ActionImpossible("Demande dépassée : le chargement a changé depuis ; la refuser.")
        chargement.confirmer_reception(par=validation.demandee_par)
        chargement.save()
    actualiser_tournee(chargement.tournee)
    return validation


# --- Départ de la tournée (§10, §11) -----------------------------------------------------


@transaction.atomic
def demarrer_tournee(tournee):
    """Le livreur démarre la tournée (§7) : l'heure de départ et la liste des commandes
    chargées sont figées, chaque commande passe « En livraison » et une livraison
    « En attente » est créée par chargement, avec ses lignes prévues."""
    tournee = _verrouiller(tournee)
    _transition(
        tournee,
        "demarrer",
        "Des commandes ne sont pas reçues : les retirer ou faire accepter une exception.",
    )
    chargements = list(tournee.chargements_actifs().select_related("commande"))
    for chargement in chargements:
        commande = Commande.objects.select_for_update().get(pk=chargement.commande_id)
        if not can_proceed(commande.partir_en_livraison):
            raise ActionImpossible(
                f"{commande} est au statut « {commande.get_statut_display()} » : "
                "la retirer de la tournée."
            )
        commande.partir_en_livraison()
        commande.save()
        livraison = Livraison.objects.create(chargement=chargement, commande=commande)
        retenues = chargement.quantites_retenues()
        for ligne in chargement.lignes.all():
            LigneLivraison.objects.create(
                livraison=livraison,
                ligne_commande_id=ligne.ligne_commande_id,
                quantite_prevue=_quantite_prevue(ligne, retenues[ligne.pk]),
            )
    tournee.demarrer()
    tournee.save()
    return tournee


def _quantite_prevue(ligne, retenue):
    """Prévu de la livraison : la quantité reçue par le livreur ; à défaut (départ par
    exception), la quantité retenue au chargement, puis l'attendu (décision 8.3)."""
    for quantite in (ligne.quantite_recue_chauffeur, retenue, ligne.quantite_attendue):
        if quantite is not None:
            return quantite


# --- Livraison (§6, §7, §12 ; décisions 8.4) -----------------------------------------------


class Resultat(models.TextChoices):
    """Issue choisie par le livreur (maquette 6) ; « Refusée » et « Client absent » sont
    des échecs avec leur motif."""

    LIVREE = "livree", "Livrée"
    PARTIELLE = "partielle", "Partielle"
    ECHEC = "echec", "Échec"


LIBELLES_PREUVE = dict(TypePreuve.choices)
STATUTS_LIVRAISON_TRAITEE = [
    StatutLivraison.LIVREE,
    StatutLivraison.PARTIELLEMENT_LIVREE,
    StatutLivraison.ECHOUEE,
    StatutLivraison.REPORTEE,
]


def _verrouiller_livraison(livraison):
    """Verrouille la tournée puis la livraison (même ordre partout)."""
    _verrouiller(livraison.chargement.tournee)
    return (
        Livraison.objects.select_for_update()
        .select_related("chargement__tournee", "commande__client")
        .get(pk=livraison.pk)
    )


def _coordonnee(valeur, limite):
    if valeur in (None, ""):
        return None
    try:
        coordonnee = Decimal(str(valeur)).quantize(Decimal("0.000001"))
    except (InvalidOperation, TypeError, ValueError):
        raise ActionImpossible("Position GPS invalide.") from None
    if not coordonnee.is_finite() or abs(coordonnee) > limite:
        raise ActionImpossible("Position GPS invalide.")
    return coordonnee


def _enregistrer_position(livraison, latitude, longitude):
    latitude, longitude = _coordonnee(latitude, 90), _coordonnee(longitude, 180)
    if latitude is not None and longitude is not None:
        livraison.latitude, livraison.longitude = latitude, longitude


def libelle_exigences(exigences):
    """« photo du bordereau ou nom du réceptionnaire ; signature »."""
    return " ; ".join(
        " ou ".join(LIBELLES_PREUVE.get(moyen, moyen).lower() for moyen in exigence)
        for exigence in exigences
    )


def message_preuves(livraison):
    """« Preuve manquante : photo du bordereau ou nom du réceptionnaire. »"""
    return f"Preuve manquante : {libelle_exigences(preuves_manquantes(livraison))}."


@transaction.atomic
def arriver_chez_client(livraison, latitude=None, longitude=None):
    """« Je suis arrivé » : heure, et position si le téléphone la donne (§6)."""
    livraison = _verrouiller_livraison(livraison)
    _autoriser(livraison, "arriver")
    _enregistrer_position(livraison, latitude, longitude)
    _transition(livraison, "arriver", "Position GPS requise pour ce client : l'autoriser.")()
    livraison.save()
    return livraison


def _preuves_jointes(livraison, photo, signature):
    """Photo du bordereau et signature : fichiers privés, liés à la livraison (§12.2)."""
    for fichier, type_preuve in (
        (photo, TypePreuve.PHOTO_BORDEREAU),
        (signature, TypePreuve.SIGNATURE),
    ):
        if fichier:
            piece = joindre_fichier(livraison, fichier, PieceJointe.Type.PHOTO)
            Preuve.objects.create(livraison=livraison, type=type_preuve, piece=piece)


def _saisie(resultat, quantites, motif, accord, champs):
    """Saisie du livreur, sous une forme enregistrable dans une demande de validation."""
    if resultat not in Resultat.values:
        raise ActionImpossible("Choisir le résultat de la livraison.")
    if motif and motif not in MotifEchec.values:
        raise ActionImpossible("Motif inconnu.")
    # Sans nom, l'accord manque : message donné par `_appliquer_saisie`.
    if (
        accord
        and (accord.get("nom_client") or "").strip()
        and (accord.get("moyen") not in MoyenAccord.values)
    ):
        raise ActionImpossible("Indiquer comment le client a donné son accord.")
    return {
        "resultat": resultat,
        "lignes": {
            str(getattr(ligne, "pk", ligne)): str(_quantite(valeur))
            for ligne, valeur in (quantites or {}).items()
        },
        "motif": motif or "",
        "accord": {k: (accord.get(k) or "").strip() for k in ("nom_client", "moyen")}
        if accord
        else None,
        "champs": {nom: (valeur or "").strip() for nom, valeur in champs.items()},
    }


def _appliquer_saisie(livraison, saisie):
    """Quantités, réceptionnaire, motif, accord, puis transition (sans contrôle du rôle :
    vérifié par l'appelant). Retourne la méthode de transition à appeler."""
    for nom, valeur in saisie["champs"].items():
        setattr(livraison, nom, valeur)
    livraison.motif_echec = saisie["motif"]
    resultat = saisie["resultat"]
    if resultat == Resultat.ECHEC:
        if not livraison.motif_echec:
            raise ActionImpossible("Indiquer le motif de l'échec.")
        return livraison.echouer, "Échec impossible depuis ce statut."

    lignes = list(livraison.lignes.select_related("ligne_commande__produit"))
    if sorted(saisie["lignes"]) != sorted(str(ligne.pk) for ligne in lignes):
        raise ActionImpossible("Indiquer la quantité livrée pour chaque produit.")
    for ligne in lignes:
        livree = Decimal(saisie["lignes"][str(ligne.pk)])
        if livree > ligne.quantite_prevue:
            raise ActionImpossible(
                f"{ligne.ligne_commande.produit} : jamais plus que le prévu "
                f"({ligne.quantite_prevue.normalize():f})."
            )
        ligne.quantite_livree = livree
        ligne.save()
    if livraison.statut == StatutLivraison.EN_ATTENTE:
        # Arrivée non signalée : elle est datée de la validation.
        if not can_proceed(livraison.arriver):
            raise ActionImpossible("Position GPS requise pour ce client : l'autoriser.")
        livraison.arriver()
    if resultat == Resultat.LIVREE:
        if not livraison.tout_livre():
            raise ActionImpossible("Quantités inférieures au prévu : choisir « Partielle ».")
        if not livraison.preuves_presentes():
            raise ActionImpossible(message_preuves(livraison))
        return livraison.livrer, "Livraison impossible."

    if not livraison.motif_echec:
        raise ActionImpossible("Indiquer le motif de la livraison partielle.")
    accord = saisie["accord"]
    if not accord or not accord["nom_client"]:
        raise ActionImpossible("Livraison partielle : enregistrer l'accord du client.")
    AccordClient.objects.create(livraison=livraison, objet=ObjetAccord.PARTIELLE, **accord)
    if not livraison.preuves_presentes():
        raise ActionImpossible(message_preuves(livraison))
    return (
        livraison.livrer_partiellement,
        "Livraison partielle : au moins un produit sous le prévu et un produit livré.",
    )


def _apres_livraison(livraison):
    """Cascade (§7) : reliquat, statut de la commande, fin de tournée."""
    commande = Commande.objects.select_for_update().get(pk=livraison.commande_id)
    if livraison.statut == StatutLivraison.PARTIELLEMENT_LIVREE:
        for ligne in livraison.lignes.all():
            if ligne.manque > 0:
                Reliquat.objects.create(
                    commande_origine=commande,
                    livraison=livraison,
                    ligne_commande_id=ligne.ligne_commande_id,
                    quantite=ligne.manque,
                )
    transition = {
        StatutLivraison.LIVREE: "cloturer_livree",
        StatutLivraison.PARTIELLEMENT_LIVREE: "cloturer_partielle",
        StatutLivraison.ECHOUEE: "signaler_anomalie",
    }[livraison.statut]
    methode = getattr(commande, transition)
    if not can_proceed(methode):
        raise ActionImpossible(
            f"{commande} est au statut « {commande.get_statut_display()} » : "
            "la livraison ne peut pas la clôturer."
        )
    methode()
    commande.save()
    tournee = Tournee.objects.select_for_update().get(pk=livraison.chargement.tournee_id)
    if can_proceed(tournee.terminer):
        tournee.terminer()
        tournee.save()
    return commande


@transaction.atomic
def cloturer_livraison(
    livraison,
    resultat,
    *,
    quantites=None,
    motif="",
    accord=None,
    photo=None,
    signature=None,
    latitude=None,
    longitude=None,
    receptionnaire_nom="",
    receptionnaire_fonction="",
    numero_bordereau="",
    commentaire="",
):
    """Le livreur valide la livraison (maquette 6) : quantités réelles, preuve, accord
    client si partielle, motif si échec ; cascade vers la commande et la tournée (§7).

    Pour un prestataire sans compte, le gestionnaire saisit à sa place : les preuves
    sont jointes, et la saisie n'est appliquée qu'à l'acceptation par un autre
    responsable (décision 8.4).
    """
    livraison = _verrouiller_livraison(livraison)
    _exiger("logistique.livrer", livraison)
    if livraison.statut not in (StatutLivraison.EN_ATTENTE, StatutLivraison.SUR_PLACE):
        raise ActionImpossible(f"Livraison déjà traitée ({livraison.get_statut_display()}).")
    saisie = _saisie(
        resultat,
        quantites,
        motif,
        accord,
        {
            "receptionnaire_nom": receptionnaire_nom,
            "receptionnaire_fonction": receptionnaire_fonction,
            "numero_bordereau": numero_bordereau,
            "commentaire": commentaire,
        },
    )
    _enregistrer_position(livraison, latitude, longitude)
    _preuves_jointes(livraison, photo, signature)

    if not livreur_affecte(_utilisateur(), livraison):
        if _exceptions_livraison(livraison, Validation.Decision.EN_ATTENTE).exists():
            raise ActionImpossible("Une saisie attend déjà la décision d'un responsable.")
        livraison.save()
        demander_validation(
            livraison,
            Validation.Action.EXCEPTION,
            motif=f"Livraison saisie pour le prestataire {livraison.tournee.livreur}",
            contenu={"objet": "livraison", **saisie},
        )
        return livraison

    methode, message = _appliquer_saisie(livraison, saisie)
    if not can_proceed(methode):
        raise ActionImpossible(message)
    methode()
    livraison.save()
    _apres_livraison(livraison)
    return livraison


def _exceptions_livraison(livraison, *decisions):
    return Validation.objects.filter(
        content_type=ContentType.objects.get_for_model(Livraison),
        object_id=livraison.pk,
        action=Validation.Action.EXCEPTION,
        decision__in=decisions,
    )


@transaction.atomic
def decider_livraison_prestataire(validation, *, accepter):
    """Décision d'un autre responsable sur une livraison saisie pour un prestataire sans
    compte (8.4). Acceptée : la saisie est appliquée, avec sa cascade."""
    livraison = _verrouiller_livraison(validation.objet)
    _exiger("logistique.valider_exception", livraison.chargement)
    validation = decider_validation(validation, accepter=accepter)
    if not accepter:
        return validation
    if livraison.statut not in (StatutLivraison.EN_ATTENTE, StatutLivraison.SUR_PLACE):
        raise ActionImpossible("Demande dépassée : la livraison a changé depuis ; la refuser.")
    methode, message = _appliquer_saisie(livraison, validation.contenu)
    if not can_proceed(methode):
        raise ActionImpossible(message)
    methode()
    livraison.save()
    _apres_livraison(livraison)
    return validation


@transaction.atomic
def reporter_livraison(livraison, nouvelle_date, consigne=""):
    """Le gestionnaire fixe une nouvelle date (§6, décision 8.4) : la commande repasse
    « À planifier » à cette date et entre dans le plan de production de ce jour, où elle
    est entièrement reproduite. Le plan de l'ancienne date n'est pas recalculé."""
    from production.services import commande_modifiee

    livraison = _verrouiller_livraison(livraison)
    _autoriser(livraison, "reporter")
    if nouvelle_date is None or nouvelle_date < timezone.localdate():
        raise ActionImpossible("Choisir une nouvelle date, à partir d'aujourd'hui.")
    livraison.reportee_au = nouvelle_date
    livraison.reporter()
    livraison.save()

    commande = Commande.objects.select_for_update().get(pk=livraison.commande_id)
    commande.date_livraison = nouvelle_date
    if (consigne or "").strip():
        commande.instructions = consigne.strip()
    _transition(commande, "relancer", "La commande ne peut pas être relancée.")()
    commande.save()
    commande_modifiee(commande)
    return livraison


# --- Retours et incidents (§12.3, décision 8.4) --------------------------------------------


@transaction.atomic
def declarer_retour(livraison, *, produit, quantite, motif, etat):
    """Retour de marchandise depuis une livraison traitée : par le livreur sur place, ou
    par le gestionnaire au retour au dépôt (8.4)."""
    livraison = _verrouiller_livraison(livraison)
    _exiger("logistique.declarer_retour", livraison)
    if livraison.statut not in STATUTS_LIVRAISON_TRAITEE:
        raise ActionImpossible("Retour possible une fois la livraison traitée.")
    _meme_entreprise(produit)
    prevues = {
        ligne.ligne_commande.produit_id: ligne.quantite_prevue
        for ligne in livraison.lignes.select_related("ligne_commande")
    }
    if produit.pk not in prevues:
        raise ActionImpossible(f"{produit} ne fait pas partie de cette livraison.")
    quantite = _quantite(quantite)
    if quantite <= 0 or quantite > prevues[produit.pk]:
        raise ActionImpossible("Quantité retournée invalide (au plus la quantité prévue).")
    if etat not in EtatRetour.values:
        raise ActionImpossible("Indiquer l'état de la marchandise.")
    if not (motif or "").strip():
        raise ActionImpossible("Indiquer le motif du retour.")
    return Retour.objects.create(
        livraison=livraison, produit=produit, quantite=quantite, motif=motif.strip(), etat=etat
    )


@transaction.atomic
def decider_retour(retour, decision):
    """Décision du responsable (Gestionnaire, Direction) sur un retour (8.4)."""
    retour = Retour.objects.select_for_update().get(pk=retour.pk)
    _exiger("logistique.decider_retour", retour)
    if retour.decision:
        raise ActionImpossible("Décision déjà prise sur ce retour.")
    if decision not in DecisionRetour.values:
        raise ActionImpossible("Choisir une décision.")
    retour.decision = decision
    retour.decide_par = _utilisateur()
    retour.decide_le = timezone.now()
    retour.save()
    return retour


@transaction.atomic
def signaler_incident(tournee, type, commentaire=""):
    """Panne, accident… signalé par le livreur pendant la tournée ; sans effet sur les
    statuts (décision 8.4)."""
    tournee = _verrouiller(tournee)
    _exiger("logistique.livrer", tournee)
    if tournee.statut != StatutTournee.EN_COURS:
        raise ActionImpossible("Incident à signaler pendant la tournée.")
    if type not in TypeIncident.values:
        raise ActionImpossible("Choisir le type d'incident.")
    return Incident.objects.create(tournee=tournee, type=type, commentaire=commentaire.strip())
