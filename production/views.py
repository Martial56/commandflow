"""Écran Plan de production (maquette 13) et fiche atelier imprimable."""

import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from commandes.models import Commande, LigneCommande, Statut
from entreprises.parametres import heure_limite_confirmation
from noyau.contexte import entreprise_obligatoire

from . import services
from .models import BesoinProduit, PlanProduction
from .services import STATUTS_PLANIFIES

DETAIL_MAX = 5  # commandes listées dans le détail d'un produit, puis « + N autres »


def _exiger(request, permission, objet=None):
    if not request.user.has_perm(permission, objet):
        raise PermissionDenied


def _date(request):
    try:
        return datetime.date.fromisoformat(request.GET.get("date", ""))
    except ValueError:
        return timezone.localdate() + datetime.timedelta(days=1)


def _retour(plan):
    return redirect(f"{reverse('ecran_production')}?date={plan.date_livraison.isoformat()}")


def _detail(date, besoin):
    """Commandes du plan contenant ce produit, des plus grosses aux plus petites."""
    lignes = (
        LigneCommande.objects.filter(
            retiree=False,
            produit=besoin.produit,
            commande__date_livraison=date,
            commande__statut__in=STATUTS_PLANIFIES,
        )
        .values("commande__client__nom")
        .annotate(total=Sum("quantite"))
        .order_by("-total", "commande__client__nom")
    )
    lignes = list(lignes)
    return lignes[:DETAIL_MAX], max(len(lignes) - DETAIL_MAX, 0)


@login_required
def plan(request):
    """Plan de production d'une date de livraison (maquette 13)."""
    _exiger(request, "ecrans.production")
    date = _date(request)
    plan = PlanProduction.objects.filter(date_livraison=date, creneau="").first()
    contexte = {"date": date, "plan": plan}
    commandes = Commande.objects.filter(date_livraison=date)
    contexte["recurrentes_a_confirmer"] = commandes.filter(
        statut=Statut.A_CONFIRMER, modele_recurrent__isnull=False
    ).count()
    contexte["heure_limite"] = heure_limite_confirmation(entreprise_obligatoire())
    if plan is not None:
        besoins = list(plan.besoins.select_related("produit").order_by("produit__nom"))
        choisi = next(
            (b for b in besoins if str(b.produit_id) == request.GET.get("produit")),
            besoins[0] if besoins else None,
        )
        detail, autres = _detail(date, choisi) if choisi else ([], 0)
        contexte.update(
            besoins=besoins,
            choisi=choisi,
            detail=detail,
            autres=autres,
            nb_commandes=commandes.filter(statut__in=STATUTS_PLANIFIES).count(),
            nb_en_preparation=commandes.filter(statut=Statut.EN_PREPARATION).count(),
            nb_pretes=commandes.filter(statut=Statut.PRETE_CHARGEMENT).count(),
            ajoutees=plan.ajouts_apres_validation,
            modifiees=plan.changements_apres_validation,
            en_manque=[b for b in besoins if b.quantite_manquante > 0],
            peut_gerer=request.user.has_perm("production.gerer_plan", plan),
            peut_preparer=request.user.has_perm("production.saisir_production", plan),
        )
    return render(request, "production/plan.html", contexte)


@login_required
@require_POST
def ajuster(request, pk):
    """« À produire » modifié en direct (htmx) : renvoie la ligne recalculée."""
    besoin = get_object_or_404(BesoinProduit.objects.select_related("produit", "plan"), pk=pk)
    erreur = ""
    try:
        besoin = services.ajuster_a_produire(besoin, request.POST.get("a_produire", "").strip())
    except (ValidationError, services.ActionImpossible) as exc:
        erreur = " ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
        besoin.refresh_from_db()
    except ArithmeticError:
        erreur = "Saisir un nombre."
    return render(
        request,
        "production/_besoin.html",
        {"besoin": besoin, "plan": besoin.plan, "peut_gerer": True, "erreur": erreur},
    )


def _action(request, pk, fonction, succes):
    plan = get_object_or_404(PlanProduction, pk=pk)
    try:
        resultat = fonction(plan)
    except services.ActionImpossible as erreur:
        messages.error(request, str(erreur))
    else:
        messages.success(request, succes(resultat) if callable(succes) else succes)
    return _retour(plan)


@login_required
@require_POST
def valider(request, pk):
    return _action(
        request,
        pk,
        services.valider_plan,
        "Plan validé : les commandes passent en préparation.",
    )


@login_required
@require_POST
def cloturer(request, pk):
    return _action(request, pk, services.cloturer_plan, "Plan clôturé.")


@login_required
@require_POST
def marquer_pretes(request, pk):
    def message(resultat):
        texte = f"{len(resultat.pretes)} commande(s) prête(s) au chargement."
        if resultat.restantes:
            texte += f" {len(resultat.restantes)} restent en préparation : quantités insuffisantes."
        return texte

    return _action(request, pk, services.marquer_plan_pret, message)


@login_required
def fiche_atelier(request, pk):
    """Fiche atelier imprimable (décision 8.2 : page HTML, PDF au Sprint 6)."""
    _exiger(request, "ecrans.production")
    plan = get_object_or_404(PlanProduction, pk=pk)
    return render(
        request,
        "production/fiche_atelier.html",
        {
            "plan": plan,
            "besoins": plan.besoins.select_related("produit")
            .filter(quantite_a_produire__gt=0)
            .order_by("produit__nom"),
            "imprime_le": timezone.localtime(),
        },
    )
