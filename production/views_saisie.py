"""Saisie de production sur téléphone (maquette 2) : une fournée à la fois, cumulée."""

import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from noyau.templatetags.format_fr import quantite

from . import services
from .models import BesoinProduit, PlanProduction, SaisieProduction, StatutPlan

DERNIERES_SAISIES = 5


def unite_au_pluriel(produit, nombre):
    """« pièce » → « pièces » au-delà de 1 ; les unités au poids (kg) sont invariables."""
    unite = produit.get_unite_display()
    return unite if produit.quantites_decimales or nombre <= 1 else f"{unite}s"


def _plan_par_defaut():
    """Plan validé le plus proche à partir d'aujourd'hui (la production en cours)."""
    return (
        PlanProduction.objects.filter(
            statut=StatutPlan.VALIDE, date_livraison__gte=timezone.localdate()
        )
        .order_by("date_livraison")
        .first()
    )


def _url(plan, besoin=None):
    url = f"{reverse('production_saisie')}?date={plan.date_livraison.isoformat()}"
    return f"{url}&produit={besoin.produit_id}" if besoin else url


@login_required
def saisie(request):
    """Écran de terrain : fournée du produit choisi et état des autres produits du jour."""
    if not request.user.has_perm("production.acceder_saisie"):
        raise PermissionDenied
    try:
        date = datetime.date.fromisoformat(request.GET.get("date", ""))
        plan = PlanProduction.objects.filter(date_livraison=date, creneau="").first()
    except ValueError:
        plan = _plan_par_defaut()
    contexte = {"plan": plan}
    if plan is not None and plan.statut == StatutPlan.VALIDE:
        besoins = list(
            plan.besoins.select_related("produit")
            .filter(quantite_a_produire__gt=0)
            .order_by("produit__nom")
        )
        choisi = next(
            (b for b in besoins if str(b.produit_id) == request.GET.get("produit")),
            next((b for b in besoins if b.quantite_manquante > 0), besoins[0] if besoins else None),
        )
        if choisi is not None:
            contexte.update(
                choisi=choisi,
                progression=(
                    min(int(choisi.quantite_produite * 100 / choisi.quantite_a_produire), 100)
                    if choisi.quantite_a_produire
                    else 0
                ),
                # Proposition : ce qui manque encore (vide si l'objectif est atteint).
                quantite_proposee=(
                    quantite(choisi.quantite_manquante) if choisi.quantite_manquante else ""
                ),
                lot_propose=services.prochain_lot(choisi),
                lots=sorted(
                    set(
                        SaisieProduction.objects.filter(besoin__plan=plan).values_list(
                            "lot", flat=True
                        )
                    )
                ),
                saisies=SaisieProduction.objects.filter(besoin=choisi)
                .select_related("saisie_par")
                .order_by("-saisie_le", "-pk")[:DERNIERES_SAISIES],
                autres=[b for b in besoins if b.pk != choisi.pk],
                unite_pluriel=unite_au_pluriel(choisi.produit, 2),
                peut_corriger=request.user.has_perm("production.gerer_plan", plan),
            )
    return render(request, "production/saisie.html", contexte)


@login_required
@require_POST
def enregistrer(request, pk):
    besoin = get_object_or_404(BesoinProduit.objects.select_related("produit", "plan"), pk=pk)
    try:
        nouvelle = services.enregistrer_saisie(
            besoin,
            request.POST.get("quantite", "").replace(",", ".").replace(" ", ""),
            lot=request.POST.get("lot", ""),
            commentaire=request.POST.get("commentaire", ""),
        )
    except (ValidationError, services.ActionImpossible) as erreur:
        texte = " ".join(erreur.messages) if isinstance(erreur, ValidationError) else str(erreur)
        messages.error(request, texte)
    except ArithmeticError:
        messages.error(request, "Saisir une quantité.")
    else:
        unite = unite_au_pluriel(besoin.produit, nouvelle.quantite)
        messages.success(
            request,
            f"{besoin.produit.nom} : fournée de {quantite(nouvelle.quantite)} {unite} "
            f"enregistrée ({nouvelle.lot}).",
        )
    return redirect(_url(besoin.plan, besoin))


@login_required
@require_POST
def annuler(request, pk):
    saisie = get_object_or_404(
        SaisieProduction.objects.select_related("besoin__plan", "besoin__produit"), pk=pk
    )
    try:
        services.annuler_saisie(saisie, request.POST.get("motif", ""))
    except (ValidationError, services.ActionImpossible) as erreur:
        texte = " ".join(erreur.messages) if isinstance(erreur, ValidationError) else str(erreur)
        messages.error(request, texte)
    else:
        messages.success(request, f"Saisie « {saisie.lot} » annulée.")
    return redirect(_url(saisie.besoin.plan, saisie.besoin))
