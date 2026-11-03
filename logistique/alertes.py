"""Alertes de la logistique affichées à l'écran (décision 8.4 ; envoi réel au Sprint 6)."""

from noyau.contexte import entreprise_courante

from .models import Retour


def retours_a_decider():
    return Retour.objects.filter(decision="")


def nombre_alertes(utilisateur):
    """Pastille du menu Tournées : retours en attente de la décision d'un responsable."""
    if entreprise_courante() is None:
        return 0
    return retours_a_decider().count()
