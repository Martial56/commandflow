"""Services de production : toute écriture du plan, des besoins et des saisies passe ici.

Cascades (§7 de modele-donnees.md) :
- commande confirmée → intégrée au plan de sa date, besoins recalculés (`integrer_commande`) ;
- commande modifiée ou annulée après confirmation → besoins recalculés (`recalculer_besoin`).

Le plan est verrouillé (`select_for_update`) pendant chaque recalcul : deux confirmations
simultanées ne peuvent pas perdre de quantité.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone
from django_fsm import can_proceed

from commandes.models import Commande, LigneCommande, Statut
from noyau.contexte import entreprise_obligatoire, utilisateur_courant

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


# --- Plan : ajustement, validation, clôture -------------------------------------------


class ActionImpossible(Exception):
    """Action refusée par une règle métier ; le message s'affiche tel quel."""


def _utilisateur():
    utilisateur = utilisateur_courant()
    if utilisateur is None:
        raise PermissionDenied("Action à attribuer à un utilisateur connecté.")
    return utilisateur


def _exiger(permission, objet):
    if not _utilisateur().has_perm(permission, objet):
        raise PermissionDenied("Votre rôle ne permet pas cette action.")


@transaction.atomic
def ajuster_a_produire(besoin, quantite):
    """Objectif de production d'un produit, fixé par un responsable (8.2).

    Possible avant et après validation (tracé par l'historique), pas après clôture.
    """
    plan = PlanProduction.objects.select_for_update().get(pk=besoin.plan_id)
    _exiger("production.gerer_plan", plan)
    if plan.statut == StatutPlan.CLOTURE:
        raise ActionImpossible("Le plan est clôturé : il ne peut plus être modifié.")
    besoin = BesoinProduit.objects.select_for_update().get(pk=besoin.pk)
    quantite = Decimal(str(quantite))
    if quantite < 0 or (quantite and not besoin.produit.accepte_quantite(quantite)):
        unite = besoin.produit.get_unite_display()
        raise ValidationError(
            {"quantite_a_produire": f"Quantité invalide pour l'unité « {unite} »."}
        )
    besoin.quantite_a_produire = quantite
    besoin.save()
    return besoin


@transaction.atomic
def valider_plan(plan):
    """Brouillon → validé ; les commandes du plan passent « En préparation » (§7)."""
    plan = PlanProduction.objects.select_for_update().get(pk=plan.pk)
    _exiger("production.gerer_plan", plan)
    if plan.statut != StatutPlan.BROUILLON:
        raise ActionImpossible(f"Plan déjà {plan.get_statut_display().lower()}.")
    if not can_proceed(plan.valider):
        raise ActionImpossible("Aucune commande confirmée dans ce plan.")
    plan.valider(par=_utilisateur())
    plan.save()
    for commande in Commande.objects.select_for_update().filter(
        date_livraison=plan.date_livraison, statut=Statut.A_PLANIFIER
    ):
        commande.lancer_preparation()
        commande.save()
    return plan


@transaction.atomic
def cloturer_plan(plan):
    """Validé → clôturé, quand la production du jour est terminée ; plus de saisie (8.2)."""
    plan = PlanProduction.objects.select_for_update().get(pk=plan.pk)
    _exiger("production.gerer_plan", plan)
    if plan.statut != StatutPlan.VALIDE:
        raise ActionImpossible("Seul un plan validé peut être clôturé.")
    plan.cloturer(par=_utilisateur())
    plan.save()
    return plan


# --- Saisies de production --------------------------------------------------------


def _mettre_a_jour_produit(besoin):
    """Cumul produit du besoin = somme de ses saisies non annulées (§9)."""
    total = besoin.saisies.filter(annulee=False).aggregate(total=Sum("quantite"))["total"]
    besoin.quantite_produite = total or ZERO
    besoin.save(update_fields=["quantite_produite"])
    return besoin


def prochain_lot(besoin):
    """Libellé proposé pour la fournée suivante : « Lot N+1 » (décision 8.2)."""
    return f"Lot {besoin.saisies.count() + 1}"


@transaction.atomic
def enregistrer_saisie(besoin, quantite, lot="", commentaire=""):
    """Nouvelle fournée : s'ajoute aux saisies précédentes, sans jamais les remplacer (§9).

    Plan validé uniquement (ni brouillon, ni clôturé). Production et Gestionnaire (8.2).
    """
    plan = PlanProduction.objects.select_for_update().get(pk=besoin.plan_id)
    _exiger("production.saisir_production", plan)
    if plan.statut == StatutPlan.BROUILLON:
        raise ActionImpossible("Le plan n'est pas encore validé : la production n'a pas commencé.")
    if plan.statut == StatutPlan.CLOTURE:
        raise ActionImpossible("Le plan est clôturé : plus aucune saisie n'est possible.")
    besoin = BesoinProduit.objects.select_for_update().get(pk=besoin.pk)
    quantite = Decimal(str(quantite))
    if not besoin.produit.accepte_quantite(quantite):
        unite = besoin.produit.get_unite_display()
        raise ValidationError({"quantite": f"Quantité invalide pour l'unité « {unite} »."})
    saisie = SaisieProduction(
        entreprise=plan.entreprise,
        besoin=besoin,
        lot=(lot or "").strip() or prochain_lot(besoin),
        quantite=quantite,
        saisie_par=_utilisateur(),
        commentaire=(commentaire or "").strip(),
    )
    saisie.full_clean()
    saisie.save()
    _mettre_a_jour_produit(besoin)
    return saisie


@transaction.atomic
def annuler_saisie(saisie, motif):
    """Corrige une erreur : la saisie est annulée (motif obligatoire), jamais modifiée (§3.4).

    Par son auteur ou par un responsable du plan, tant que le plan n'est pas clôturé (8.2).
    """
    saisie = SaisieProduction.objects.select_for_update().select_related("besoin").get(pk=saisie.pk)
    utilisateur = _utilisateur()
    auteur = saisie.saisie_par_id == utilisateur.pk
    plan = saisie.besoin.plan
    if not (
        (auteur and utilisateur.has_perm("production.saisir_production", plan))
        or utilisateur.has_perm("production.gerer_plan", plan)
    ):
        raise PermissionDenied("Seul l'auteur de la saisie ou un responsable peut l'annuler.")
    if plan.statut == StatutPlan.CLOTURE:
        raise ActionImpossible("Le plan est clôturé : les saisies ne peuvent plus être annulées.")
    if saisie.annulee:
        raise ActionImpossible("Cette saisie est déjà annulée.")
    motif = (motif or "").strip()
    if not motif:
        raise ValidationError({"motif": "Indiquer le motif de l'annulation."})
    saisie.annulee = True
    saisie.motif_annulation = motif
    saisie.annulee_par = utilisateur
    saisie.annulee_le = timezone.now()
    saisie.save(update_fields=["annulee", "motif_annulation", "annulee_par", "annulee_le"])
    _mettre_a_jour_produit(saisie.besoin)
    return saisie


# --- Commandes prêtes au chargement -----------------------------------------------


@dataclass
class ResultatPreparation:
    pretes: list = field(default_factory=list)
    restantes: list = field(default_factory=list)  # quantités insuffisantes


@transaction.atomic
def marquer_plan_pret(plan):
    """Marque prêtes toutes les commandes couvertes du plan, dans l'ordre des créneaux (8.2).

    Chaque commande réserve ses quantités avant la suivante : celles que le disponible ne
    couvre plus restent « En préparation » (exception possible).
    """
    from commandes import services as commandes

    plan = PlanProduction.objects.select_for_update().get(pk=plan.pk)
    _exiger("production.saisir_production", plan)  # Production, Gestionnaire (§4)
    resultat = ResultatPreparation()
    a_preparer = Commande.objects.filter(
        date_livraison=plan.date_livraison, statut=Statut.EN_PREPARATION
    ).order_by("creneau", "numero")
    for commande in a_preparer:
        try:
            with transaction.atomic():
                resultat.pretes.append(commandes.marquer_prete(commande))
        except commandes.ActionImpossible:
            resultat.restantes.append(commande)
    return resultat


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
    _aligner_sur_le_plan(commande)


def _aligner_sur_le_plan(commande):
    """Statut de la commande cohérent avec le plan de sa (nouvelle) date (décision 8.2)."""
    if commande.statut == Statut.EN_PREPARATION and can_proceed(commande.replanifier):
        commande.replanifier()
        commande.save()
    elif commande.statut == Statut.A_PLANIFIER and can_proceed(commande.lancer_preparation):
        commande.lancer_preparation()
        commande.save()
