"""Services de la logistique : toute écriture des tournées et des chargements passe ici.

Les vues appellent ces services ; les services vérifient les droits de l'utilisateur
courant et appellent les transitions (§5 de modele-donnees.md, décisions 8.3). La tournée
est verrouillée (`select_for_update`) pendant chaque modification de ses chargements.
"""

from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Max
from django_fsm import can_proceed

from commandes.models import Commande, Statut
from comptes.models import Role
from entreprises.parametres import creneaux
from noyau.contexte import AutreEntreprise, entreprise_obligatoire, utilisateur_courant
from noyau.models import Validation
from noyau.services import decider_validation, demander_validation, prochain_numero

from .etat import exceptions
from .models import (
    Chargement,
    LigneChargement,
    MotifEcart,
    StatutChargement,
    StatutTournee,
    Tournee,
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
    """Fixe l'ordre de passage : `chargements` contient tous les chargements actifs."""
    tournee = _verrouiller(tournee)
    _exiger("logistique.gerer_tournee", tournee)
    _exiger_modifiable(tournee)
    actifs = {chargement.pk: chargement for chargement in tournee.chargements_actifs()}
    ordre = [getattr(chargement, "pk", chargement) for chargement in chargements]
    if sorted(ordre) != sorted(actifs):
        raise ActionImpossible("L'ordre doit reprendre toutes les commandes de la tournée.")
    _numeroter(tournee, [actifs[pk] for pk in ordre])
    return tournee


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
