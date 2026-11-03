"""Alertes de la logistique affichées à l'écran (décision 8.4 ; envoi réel au Sprint 6)."""

from django.contrib.contenttypes.models import ContentType

from commandes.models import Commande, Statut
from noyau.contexte import entreprise_courante
from noyau.models import Validation

from .models import Livraison, Retour


def retours_a_decider():
    return Retour.objects.filter(decision="")


def commandes_en_anomalie():
    """Livraison échouée, ni reportée ni abandonnée : le gestionnaire doit agir."""
    return Commande.objects.filter(statut=Statut.EN_ANOMALIE)


def livraisons_a_valider():
    """Saisies du gestionnaire pour un prestataire sans compte, en attente (8.4)."""
    return Validation.objects.filter(
        content_type=ContentType.objects.get_for_model(Livraison),
        action=Validation.Action.EXCEPTION,
        decision=Validation.Decision.EN_ATTENTE,
    )


def nombre_alertes(utilisateur):
    """Pastille du menu Tournées : retours à décider, commandes en anomalie, saisies de
    prestataire à valider."""
    if entreprise_courante() is None:
        return 0
    return (
        retours_a_decider().count()
        + commandes_en_anomalie().count()
        + livraisons_a_valider().count()
    )
