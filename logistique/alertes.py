"""Alertes de la logistique affichées à l'écran (décision 8.4 ; envoi réel au Sprint 6)."""

from django.contrib.contenttypes.models import ContentType

from commandes.models import Commande, Statut
from noyau.contexte import entreprise_courante
from noyau.models import Validation

from .models import Livraison, Reliquat, Retour


def retours_a_decider():
    return Retour.objects.filter(decision="")


def commandes_en_anomalie():
    """Livraison échouée, ni reportée ni abandonnée : le gestionnaire doit agir."""
    return Commande.objects.filter(statut=Statut.EN_ANOMALIE)


def reliquats_a_planifier():
    """Commandes dont le reliquat attend une date (ni planifié, ni dans une tournée)."""
    return (
        Reliquat.objects.filter(traite=False, nouvelle_date__isnull=True, chargement__isnull=True)
        .values("commande_origine")
        .distinct()
    )


def livraisons_a_valider():
    """Demandes en attente sur une livraison : saisie pour un prestataire sans compte,
    solde d'un reliquat (8.4)."""
    return Validation.objects.filter(
        content_type=ContentType.objects.get_for_model(Livraison),
        action=Validation.Action.EXCEPTION,
        decision=Validation.Decision.EN_ATTENTE,
    )


def nombre_alertes(utilisateur):
    """Pastille du menu Tournées : retours à décider, commandes en anomalie, reliquats
    sans date, demandes sur les livraisons (saisies de prestataire, soldes)."""
    if entreprise_courante() is None:
        return 0
    return (
        retours_a_decider().count()
        + commandes_en_anomalie().count()
        + reliquats_a_planifier().count()
        + livraisons_a_valider().count()
    )
