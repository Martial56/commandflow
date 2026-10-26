"""Alertes de production affichées à l'écran (décision 8.2 ; envoi réel au Sprint 6)."""

from django.db.models import F
from django.utils import timezone

from noyau.contexte import entreprise_courante

from .models import BesoinProduit, StatutPlan


def besoins_en_manque():
    """Besoins des plans validés, non clôturés, d'aujourd'hui ou plus tard, encore en manque."""
    return BesoinProduit.objects.filter(
        plan__statut=StatutPlan.VALIDE,
        plan__date_livraison__gte=timezone.localdate(),
        quantite_a_produire__gt=F("quantite_produite"),
    )


def nombre_alertes(utilisateur):
    """Pastille du menu Production : nombre de produits encore en manque."""
    if entreprise_courante() is None:
        return 0
    return besoins_en_manque().count()
