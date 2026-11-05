"""Alertes des paiements affichées à l'écran (§16.1 : paiement en retard ; décision 8.5)."""

from django.utils import timezone

from noyau.contexte import entreprise_courante

from .services import factures_avec_solde


def factures_en_retard():
    """Factures émises dont l'échéance est passée et qui restent dues."""
    return factures_avec_solde().filter(solde__gt=0, echeance__lt=timezone.localdate())


def nombre_alertes(utilisateur):
    """Pastille du menu Paiements : factures en retard de paiement."""
    if entreprise_courante() is None:
        return 0
    return factures_en_retard().count()
