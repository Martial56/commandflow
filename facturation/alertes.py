"""Alertes de la facturation affichées à l'écran (§16.1 : facture à traiter, document
manquant ; décision 8.5)."""

from noyau.contexte import entreprise_courante

from .models import Facture, StatutFacture
from .services import livraisons_a_facturer


def documents_manquants():
    """Documents émis dont le PDF n'est pas encore joint."""
    return Facture.objects.filter(statut=StatutFacture.EMISE, piece__isnull=True)


def nombre_alertes(utilisateur):
    """Pastille du menu Facturation : livraisons à facturer et documents manquants."""
    if entreprise_courante() is None:
        return 0
    return livraisons_a_facturer().count() + documents_manquants().count()
