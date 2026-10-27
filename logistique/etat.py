"""Lectures sur la logistique utilisées par les conditions de transition des commandes."""

from django.contrib.contenttypes.models import ContentType

from noyau.models import Validation

from .models import Chargement, StatutChargement


def exception_acceptee(chargement, objet):
    """Validation d'exception acceptée sur ce chargement, pour `objet` (« ecart » ou « depart »)."""
    return Validation.objects.filter(
        content_type=ContentType.objects.get_for_model(Chargement),
        object_id=chargement.pk,
        action=Validation.Action.EXCEPTION,
        decision=Validation.Decision.ACCEPTEE,
        contenu__objet=objet,
    ).exists()


def chargement_actif(commande):
    return (
        Chargement.objects.filter(commande=commande)
        .exclude(statut=StatutChargement.RETIRE)
        .select_related("tournee")
        .order_by("-pk")
        .first()
    )


def peut_partir(chargement):
    """Reçu par le livreur, ou départ autorisé par exception (§4, §5, décision 8.3)."""
    return chargement.statut == StatutChargement.RECUE_CHAUFFEUR or exception_acceptee(
        chargement, "depart"
    )


def commande_chargee(commande):
    """La commande peut partir en livraison : son chargement actif peut partir."""
    chargement = chargement_actif(commande)
    return chargement is not None and peut_partir(chargement)
