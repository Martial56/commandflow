"""Services des commandes : toute écriture passe ici, dans une transaction.

Les vues appellent ces services ; les services vérifient les droits de l'utilisateur
courant et appellent les transitions du modèle. Effets sur la production (recalcul du
besoin, alerte) : Sprint 3.
"""

import datetime
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django_fsm import can_proceed

from catalogue.models import Produit
from clients.models import Adresse
from entreprises.parametres import creneaux
from noyau.contexte import AutreEntreprise, entreprise_obligatoire, utilisateur_courant
from noyau.models import PieceJointe, Validation
from noyau.services import (
    decider_validation,
    demander_validation,
    joindre_fichier,
    prochain_numero,
)

from .models import (
    STATUTS_ANNULATION_SIMPLE,
    STATUTS_ANNULATION_VALIDEE,
    STATUTS_MODIFICATION_LIBRE,
    STATUTS_MODIFICATION_VALIDEE,
    Commande,
    LigneCommande,
    Statut,
)

# Champs d'en-tête modifiables (les lignes passent par `lignes`).
CHAMPS_MODIFIABLES = {"adresse", "date_livraison", "creneau", "instructions", "urgente", "canal"}
# Champs qu'une demande de modification après confirmation peut changer.
CHAMPS_DEMANDE_MODIFICATION = {"adresse", "date_livraison", "creneau", "instructions"}


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
    """Refuse tout objet (client, adresse, produit) d'une autre entreprise."""
    entreprise = entreprise_obligatoire()
    for objet in objets:
        if objet.entreprise_id != entreprise.pk:
            raise AutreEntreprise(f"{objet} n'appartient pas à l'entreprise courante.")


def _verrouiller(commande):
    """Relit la commande en la verrouillant (entreprise courante uniquement)."""
    return Commande.objects.select_for_update().get(pk=commande.pk)


def _validations(commande, action, *decisions):
    return Validation.objects.filter(
        content_type=ContentType.objects.get_for_model(Commande),
        object_id=commande.pk,
        action=action,
        decision__in=decisions,
    )


def _verifier_creneau(creneau):
    proposes = creneaux(entreprise_obligatoire())
    if creneau not in proposes:
        raise ValidationError(
            {"creneau": f"Créneau inconnu ; créneaux proposés : {', '.join(proposes)}."}
        )


def _verifier_adresse(client, adresse):
    _meme_entreprise(client)
    if adresse is None:
        adresse = client.adresse_par_defaut
        if adresse is None:
            raise ValidationError({"adresse": "Ce client n'a pas d'adresse de livraison."})
    _meme_entreprise(adresse)
    if adresse.client_id != client.pk or not adresse.actif:
        raise ValidationError({"adresse": "Choisir une adresse active de ce client."})
    return adresse


def _normaliser_lignes(lignes):
    """[(produit, quantité)] → {produit: Decimal}, en refusant les doublons de produit."""
    resultat = {}
    for produit, quantite in lignes:
        if produit in resultat:
            raise ValidationError({"lignes": f"{produit.nom} apparaît deux fois."})
        resultat[produit] = Decimal(str(quantite))
    return resultat


def _appliquer_lignes(commande, lignes):
    """Met les lignes actives de la commande à l'état voulu.

    - produit déjà présent : quantité modifiée, **prix figé conservé** ;
    - nouveau produit : nouvelle ligne au prix actuel du catalogue ;
    - produit absent de `lignes` : ligne marquée retirée (pas de suppression).
    """
    voulues = _normaliser_lignes(lignes)
    _meme_entreprise(*voulues)
    actuelles = {ligne.produit_id: ligne for ligne in commande.lignes.filter(retiree=False)}
    for produit, quantite in voulues.items():
        ligne = actuelles.pop(produit.pk, None)
        if ligne is None:
            if not produit.actif:
                raise ValidationError({"lignes": f"{produit.nom} n'est plus au catalogue."})
            ligne = LigneCommande(
                entreprise=commande.entreprise,
                commande=commande,
                produit=produit,
                quantite=quantite,
            )
        elif ligne.quantite == quantite:
            continue
        else:
            ligne.quantite = quantite
        ligne.full_clean(exclude=["prix_unitaire", "montant"])
        ligne.save()
    for ligne in actuelles.values():
        ligne.retiree = True
        ligne.save(update_fields=["retiree"])


# --- Création et modification ------------------------------------------------


@transaction.atomic
def creer_commande(*, client, lignes, canal, date_livraison, creneau, adresse=None, urgente=False,
                   instructions="", recue_le=None, justificatif=None):  # fmt: skip
    """Crée une commande au statut brouillon, avec son numéro CMD-AAAA-000123.

    `lignes` : liste de (produit, quantité). Le prix de chaque ligne est copié du
    catalogue (prix figé). L'année du numéro est celle de la réception.
    `justificatif` : bon de commande, photo ou capture (image ou PDF), stocké en privé.
    """
    _exiger("commandes.creer_commande")
    if not client.actif:
        raise ValidationError({"client": "Ce client est désactivé."})
    adresse = _verifier_adresse(client, adresse)
    _verifier_creneau(creneau)
    commande = Commande(
        entreprise=entreprise_obligatoire(),
        client=client,
        adresse=adresse,
        canal=canal,
        date_livraison=date_livraison,
        creneau=creneau,
        urgente=urgente,
        instructions=instructions,
        **({"recue_le": recue_le} if recue_le else {}),
    )
    commande.full_clean(exclude=["numero"])
    commande.numero = prochain_numero("CMD", date_reference=commande.recue_le.date())
    commande.save()
    _appliquer_lignes(commande, lignes)
    if justificatif is not None:
        joindre_justificatif(commande, justificatif)
    return commande


TYPES_JUSTIFICATIF = {"application/pdf": PieceJointe.Type.BON_COMMANDE}


@transaction.atomic
def joindre_justificatif(commande, fichier):
    """Bon de commande (PDF) ou photo / capture (image) joint à la commande (§8)."""
    type_mime = getattr(fichier, "content_type", "") or ""
    if type_mime.startswith("image/"):
        type_piece = PieceJointe.Type.PHOTO
    elif type_mime in TYPES_JUSTIFICATIF:
        type_piece = TYPES_JUSTIFICATIF[type_mime]
    else:
        raise ValidationError({"justificatif": "Joindre une image ou un PDF."})
    return joindre_fichier(commande, fichier, type_piece)


@transaction.atomic
def modifier_commande(commande, *, lignes=None, **champs):
    """Modifie une commande avant confirmation (brouillon ou à confirmer).

    Après confirmation, passer par `demander_modification` (validation d'un responsable).
    """
    commande = _verrouiller(commande)
    _exiger("commandes.soumettre_commande", commande)
    if commande.statut not in STATUTS_MODIFICATION_LIBRE:
        raise ActionImpossible(
            "Commande déjà confirmée : demander une modification à un responsable."
        )
    _modifier(commande, lignes=lignes, **champs)
    if commande.statut == Statut.A_CONFIRMER and commande.urgente:
        _demander_urgence(commande)
    return commande


def _modifier(commande, *, lignes=None, **champs):
    inconnus = set(champs) - CHAMPS_MODIFIABLES
    if inconnus:
        raise ValueError(f"Champs non modifiables : {', '.join(sorted(inconnus))}")
    for champ, valeur in champs.items():
        setattr(commande, champ, valeur)
    if "adresse" in champs:
        _verifier_adresse(commande.client, commande.adresse)
    if "creneau" in champs:
        _verifier_creneau(commande.creneau)
    commande.full_clean()
    commande.save()
    if lignes is not None:
        _appliquer_lignes(commande, lignes)


# --- Transitions ---------------------------------------------------------------


def _transition(commande, nom, message_conditions):
    """Vérifie rôle et conditions de la transition `nom`, avec des messages clairs."""
    methode = getattr(commande, nom)
    if not can_proceed(methode, check_conditions=False):
        raise ActionImpossible(f"Impossible depuis le statut « {commande.get_statut_display()} ».")
    # Rôle seul (has_transition_perm de django-fsm vérifierait aussi les conditions).
    if not methode._django_fsm.has_transition_perm(commande, commande.statut, _utilisateur()):
        raise PermissionDenied("Votre rôle ne permet pas cette action.")
    if not can_proceed(methode):
        raise ActionImpossible(message_conditions)
    return methode


def _demander_urgence(commande):
    """Une commande urgente déclenche une demande de validation (décision 8.1)."""
    attente = (Validation.Decision.EN_ATTENTE, Validation.Decision.ACCEPTEE)
    if not _validations(commande, Validation.Action.URGENCE, *attente).exists():
        demander_validation(commande, Validation.Action.URGENCE, motif="Commande urgente")


@transaction.atomic
def soumettre_commande(commande):
    """Brouillon → à confirmer. Une commande urgente déclenche une demande de validation."""
    commande = _verrouiller(commande)
    _transition(
        commande,
        "soumettre",
        "Compléter la commande : client, adresse, date, créneau et au moins un produit.",
    )()
    commande.save()
    if commande.urgente:
        _demander_urgence(commande)
    return commande


@transaction.atomic
def confirmer_commande(commande, note=""):
    """À confirmer → confirmée (confirmation du client ; note facultative)."""
    commande = _verrouiller(commande)
    message = (
        "Urgence non encore validée par un responsable."
        if not commande.urgence_validee()
        else "Commande incomplète."
    )
    _transition(commande, "confirmer", message)(note=note)
    commande.save()
    return commande


@transaction.atomic
def annuler_commande(commande, motif):
    """Annulation avant confirmation (motif obligatoire).

    Après confirmation, passer par `demander_annulation`.
    """
    commande = _verrouiller(commande)
    if commande.statut in STATUTS_ANNULATION_VALIDEE:
        raise ActionImpossible("Commande déjà confirmée : demander l'annulation à un responsable.")
    commande.motif_annulation = (motif or "").strip()
    _transition(commande, "annuler", "Indiquer le motif de l'annulation.")()
    commande.save()
    return commande


# --- Demandes de validation (après confirmation) ------------------------------


def _demande_en_attente(commande, action):
    if _validations(commande, action, Validation.Decision.EN_ATTENTE).exists():
        raise ActionImpossible("Une demande est déjà en attente de décision.")


@transaction.atomic
def demander_annulation(commande, motif):
    """Demande d'annulation d'une commande confirmée, soumise à un responsable."""
    commande = _verrouiller(commande)
    _exiger("commandes.creer_commande")
    if commande.statut in STATUTS_ANNULATION_SIMPLE:
        raise ActionImpossible("Commande non confirmée : l'annuler directement.")
    if commande.statut not in STATUTS_ANNULATION_VALIDEE:
        raise ActionImpossible(
            f"Annulation impossible au statut « {commande.get_statut_display()} »."
        )
    if not (motif or "").strip():
        raise ValidationError({"motif": "Indiquer le motif de l'annulation."})
    _demande_en_attente(commande, Validation.Action.ANNULATION)
    return demander_validation(commande, Validation.Action.ANNULATION, motif=motif.strip())


@transaction.atomic
def demander_modification(commande, motif, *, lignes=None, **champs):
    """Demande de modification d'une commande confirmée, soumise à un responsable.

    Le changement voulu est enregistré dans la demande et appliqué à l'acceptation.
    """
    commande = _verrouiller(commande)
    _exiger("commandes.creer_commande")
    if commande.statut not in STATUTS_MODIFICATION_VALIDEE:
        if commande.statut in STATUTS_MODIFICATION_LIBRE:
            raise ActionImpossible("Commande non confirmée : la modifier directement.")
        raise ActionImpossible(
            f"Modification impossible au statut « {commande.get_statut_display()} »."
        )
    inconnus = set(champs) - CHAMPS_DEMANDE_MODIFICATION
    if inconnus:
        raise ValueError(f"Champs non modifiables : {', '.join(sorted(inconnus))}")
    if not (motif or "").strip():
        raise ValidationError({"motif": "Indiquer le motif de la modification."})
    if lignes is None and not champs:
        raise ValidationError({"motif": "Aucun changement demandé."})
    _demande_en_attente(commande, Validation.Action.MODIFICATION)
    contenu = {"champs": _serialiser_champs(champs)}
    if lignes is not None:
        contenu["lignes"] = [
            {"produit": produit.pk, "quantite": str(quantite)}
            for produit, quantite in _normaliser_lignes(lignes).items()
        ]
    return demander_validation(
        commande, Validation.Action.MODIFICATION, motif=motif.strip(), contenu=contenu
    )


def _serialiser_champs(champs):
    resultat = {}
    for champ, valeur in champs.items():
        if isinstance(valeur, Adresse):
            valeur = valeur.pk
        elif isinstance(valeur, datetime.date):
            valeur = valeur.isoformat()
        resultat[champ] = valeur
    return resultat


def _deserialiser_champs(champs):
    resultat = dict(champs)
    if "adresse" in resultat:
        resultat["adresse"] = Adresse.objects.get(pk=resultat["adresse"])
    if "date_livraison" in resultat:
        resultat["date_livraison"] = datetime.date.fromisoformat(resultat["date_livraison"])
    return resultat


@transaction.atomic
def decider_validation_commande(validation, *, accepter):
    """Décision d'un responsable (Direction, Gestionnaire) sur une demande liée à une commande.

    Acceptée : la modification est appliquée, l'annulation exécutée ; l'urgence
    permet ensuite la confirmation. Tout est annulé si l'application échoue.
    """
    commande = _verrouiller(validation.objet)
    _exiger("commandes.valider_commande", commande)
    validation = decider_validation(validation, accepter=accepter)
    if not accepter:
        return validation

    if validation.action == Validation.Action.MODIFICATION:
        if commande.statut not in STATUTS_MODIFICATION_VALIDEE:
            raise ActionImpossible(
                f"La commande est passée au statut « {commande.get_statut_display()} »."
            )
        contenu = validation.contenu
        lignes = None
        if "lignes" in contenu:
            produits = Produit.objects.in_bulk([ligne["produit"] for ligne in contenu["lignes"]])
            lignes = [
                (produits[ligne["produit"]], ligne["quantite"]) for ligne in contenu["lignes"]
            ]
        _modifier(commande, lignes=lignes, **_deserialiser_champs(contenu.get("champs", {})))
    elif validation.action == Validation.Action.ANNULATION:
        commande.motif_annulation = validation.motif
        _transition(commande, "annuler", "Annulation impossible.")()
        commande.save()
    return validation
