from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from .contexte import (
    AucuneEntrepriseCourante,
    AucunUtilisateurCourant,
    AutreEntreprise,
    entreprise_courante,
    utilisateur_courant,
)
from .models import Compteur, PieceJointe, Validation


@transaction.atomic
def prochain_numero(prefixe, *, annuel=True, entreprise=None, date_reference=None):
    """Attribue le prochain numéro métier : `CMD-2026-000123`, ou `CLI-000123` si non annuel.

    Le compteur est verrouillé (`select_for_update`) jusqu'à la fin de la transaction :
    deux appels simultanés ne peuvent pas obtenir le même numéro, et un numéro n'est pas
    consommé si la transaction appelante est annulée.
    """
    entreprise = entreprise or entreprise_courante()
    if entreprise is None:
        raise AucuneEntrepriseCourante(f"Numérotation {prefixe} sans entreprise courante.")
    annee = (date_reference or timezone.localdate()).year if annuel else None

    compteur, _ = Compteur.objects.get_or_create(
        entreprise=entreprise, prefixe=prefixe, annee=annee
    )
    compteur = Compteur.objects.select_for_update().get(pk=compteur.pk)
    compteur.dernier_numero += 1
    compteur.save(update_fields=["dernier_numero"])

    if annuel:
        return f"{prefixe}-{annee}-{compteur.dernier_numero:06d}"
    return f"{prefixe}-{compteur.dernier_numero:06d}"


def _verifier_meme_entreprise(objet):
    entreprise = entreprise_courante()
    if entreprise is None:
        raise AucuneEntrepriseCourante("Action sur un objet métier sans entreprise courante.")
    if getattr(objet, "entreprise_id", None) != entreprise.pk:
        raise AutreEntreprise(f"{objet!r} n'appartient pas à l'entreprise courante.")


def _utilisateur_obligatoire():
    utilisateur = utilisateur_courant()
    if utilisateur is None:
        raise AucunUtilisateurCourant("Action à attribuer à un utilisateur.")
    return utilisateur


class ValidationPropreDemande(PermissionDenied):
    """L'auteur d'une demande ne peut pas la valider ni la refuser lui-même."""


@transaction.atomic
def demander_validation(objet, action, motif="", contenu=None):
    """Crée une demande de validation (en attente) sur un objet de l'entreprise courante."""
    _verifier_meme_entreprise(objet)
    return Validation.objects.create(
        objet=objet,
        action=action,
        motif=motif,
        contenu=contenu or {},
        demandee_par=_utilisateur_obligatoire(),
    )


@transaction.atomic
def decider_validation(validation, *, accepter):
    """Accepte ou refuse une validation en attente, au nom de l'utilisateur courant.

    Les profils habilités à décider sont contrôlés par le service de l'app concernée
    (par exemple `commandes.services.decider_validation_commande`), qui applique aussi
    l'effet d'une décision acceptée. Personne ne peut décider de sa propre demande
    (décision 8.1 du 19 octobre 2026).
    """
    utilisateur = _utilisateur_obligatoire()
    validation = Validation.objects.select_for_update().get(pk=validation.pk)
    if validation.demandee_par_id == utilisateur.pk:
        raise ValidationPropreDemande("Un responsable ne peut pas valider sa propre demande.")
    if accepter:
        validation.accepter(par=utilisateur)
    else:
        validation.refuser(par=utilisateur)
    validation.save()
    return validation


@transaction.atomic
def joindre_fichier(objet, fichier, type):
    """Enregistre un fichier privé lié à un objet de l'entreprise courante."""
    _verifier_meme_entreprise(objet)
    return PieceJointe.objects.create(
        objet=objet, fichier=fichier, nom_original=fichier.name, type=type
    )
