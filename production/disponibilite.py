"""Lectures sur la production utilisées par les conditions de transition des commandes."""

from .models import BesoinProduit, PlanProduction, StatutPlan


def plan_de(commande):
    """Plan de la date de livraison de la commande (un plan par jour, décision 8.2)."""
    return PlanProduction.objects.filter(date_livraison=commande.date_livraison, creneau="").first()


def plan_valide(commande):
    plan = plan_de(commande)
    return plan is not None and plan.statut == StatutPlan.VALIDE


def commande_couverte(commande):
    """Vrai si, pour chacun de ses produits, le disponible couvre sa quantité (8.2)."""
    plan = plan_de(commande)
    if plan is None:
        return False
    besoins = {b.produit_id: b for b in BesoinProduit.objects.filter(plan=plan)}
    for ligne in commande.lignes_actives:
        besoin = besoins.get(ligne.produit_id)
        if besoin is None or besoin.quantite_disponible < ligne.quantite:
            return False
    return True
