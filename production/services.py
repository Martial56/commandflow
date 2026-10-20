"""Services de production : toute écriture du plan, des besoins et des saisies passe ici.

Cascades (§7 de modele-donnees.md) :
- commande confirmée → intégrée au plan de sa date, besoins recalculés (`integrer_commande`) ;
- commande modifiée ou annulée après confirmation → besoins recalculés (`recalculer_besoin`).

Le plan est verrouillé (`select_for_update`) pendant chaque recalcul : deux confirmations
simultanées ne peuvent pas perdre de quantité.
"""

from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Sum

from commandes.models import LigneCommande, Statut
from noyau.contexte import entreprise_obligatoire

from .models import BesoinProduit, PlanProduction, SaisieProduction, StatutPlan

ZERO = Decimal("0")

# Commandes comptées dans le « commandé » du plan : confirmées et non annulées (8.2).
STATUTS_PLANIFIES = [
    Statut.CONFIRMEE,
    Statut.A_PLANIFIER,
    Statut.EN_PREPARATION,
    Statut.PRETE_CHARGEMENT,
    Statut.EN_LIVRAISON,
    Statut.LIVREE,
    Statut.PARTIELLEMENT_LIVREE,
    Statut.EN_ANOMALIE,
]
# Commandes dont les quantités sont « réservées » : prêtes au chargement et au-delà (8.2).
STATUTS_RESERVES = [
    Statut.PRETE_CHARGEMENT,
    Statut.EN_LIVRAISON,
    Statut.LIVREE,
    Statut.PARTIELLEMENT_LIVREE,
    Statut.EN_ANOMALIE,
]


# --- Plan ---------------------------------------------------------------------------


def plan_verrouille(date_livraison):
    """Plan de la date (créé s'il n'existe pas), verrouillé jusqu'à la fin de la transaction."""
    entreprise = entreprise_obligatoire()
    plan = PlanProduction.objects.filter(date_livraison=date_livraison, creneau="").first()
    if plan is None:
        try:
            with transaction.atomic():
                plan = PlanProduction.objects.create(
                    entreprise=entreprise, date_livraison=date_livraison
                )
        except IntegrityError:
            # Créé au même instant par une autre transaction : on le relit.
            plan = PlanProduction.objects.get(date_livraison=date_livraison, creneau="")
    return PlanProduction.objects.select_for_update().get(pk=plan.pk)


def _sommes_par_produit(plan, statuts):
    lignes = LigneCommande.objects.filter(
        retiree=False,
        commande__date_livraison=plan.date_livraison,
        commande__statut__in=statuts,
    )
    return {
        produit: total
        for produit, total in lignes.values("produit")
        .annotate(total=Sum("quantite"))
        .values_list("produit", "total")
    }


def recalculer_besoin(plan):
    """Recalcule les besoins du plan à partir des commandes et des saisies.

    - commandé : commandes confirmées non annulées de la date ;
    - à produire : égal au commandé pour un nouveau besoin ; ensuite, la marge fixée
      par le responsable est conservée (8.2) ;
    - produit : cumul des saisies non annulées ; réservé : commandes prêtes et au-delà.
    """
    plan = PlanProduction.objects.select_for_update().get(pk=plan.pk)
    commandes = _sommes_par_produit(plan, STATUTS_PLANIFIES)
    reserves = _sommes_par_produit(plan, STATUTS_RESERVES)
    produites = dict(
        SaisieProduction.objects.filter(besoin__plan=plan, annulee=False)
        .values("besoin__produit")
        .annotate(total=Sum("quantite"))
        .values_list("besoin__produit", "total")
    )
    besoins = {besoin.produit_id: besoin for besoin in plan.besoins.select_for_update()}
    for produit_id in set(commandes) | set(besoins):
        commande = commandes.get(produit_id, ZERO)
        besoin = besoins.get(produit_id)
        if besoin is None:
            besoin = BesoinProduit(
                entreprise=plan.entreprise,
                plan=plan,
                produit_id=produit_id,
                quantite_a_produire=commande,
            )
        else:
            besoin.quantite_a_produire = max(commande + besoin.marge, ZERO)
        besoin.quantite_commandee = commande
        besoin.quantite_produite = produites.get(produit_id, ZERO)
        besoin.quantite_reservee = reserves.get(produit_id, ZERO)
        besoin.save()
    return plan


# --- Cascades depuis les commandes ------------------------------------------------


@transaction.atomic
def integrer_commande(commande):
    """Commande confirmée → plan de sa date de livraison (§7).

    La commande passe « À planifier » ; si le plan est déjà validé, elle passe
    directement « En préparation » (décision 8.2). Action du système : aucun rôle requis.
    """
    plan = plan_verrouille(commande.date_livraison)
    commande.inclure_au_plan()
    commande.save()
    if plan.statut == StatutPlan.VALIDE:
        commande.lancer_preparation()
        commande.save()
    recalculer_besoin(plan)
    return plan


@transaction.atomic
def commande_modifiee(commande, ancienne_date=None):
    """Commande modifiée ou annulée après confirmation : besoins recalculés (§7).

    Si la date de livraison a changé, l'ancien plan et le nouveau sont recalculés.
    """
    dates = {commande.date_livraison, ancienne_date} - {None}
    for date in sorted(dates):
        plan = PlanProduction.objects.filter(date_livraison=date, creneau="").first()
        if plan is None:
            if date != commande.date_livraison or commande.statut not in STATUTS_PLANIFIES:
                continue
            plan = plan_verrouille(date)  # nouvelle date sans plan : il est créé
        recalculer_besoin(plan)
