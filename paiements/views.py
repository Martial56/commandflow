"""Écran Paiements et créances (maquette 16) et relevé de compte du client (§15 ; décisions
8.5).

Chaque action appelle un service de `paiements.services` ; les droits sont vérifiés côté
serveur (`paiements/rules.py`).
"""

import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from clients.models import Client
from facturation.models import Facture
from noyau.fichiers import url_signee

from . import services
from .models import ModePaiement, Paiement


def _exiger(request, permission, objet=None):
    if not request.user.has_perm(permission, objet):
        raise PermissionDenied


def _client(valeur):
    try:
        return Client.objects.filter(pk=int(valeur)).first()
    except (TypeError, ValueError):
        return None


def _ouvertes(client):
    """Factures restant à payer du client, avec leur statut de paiement."""
    if client is None:
        return []
    factures = list(services.factures_ouvertes(client))
    for facture in factures:
        facture.statut_paiement = services.StatutPaiement(
            services.statut_paiement(facture, reste=facture.solde)
        )
    return factures


@login_required
def ecran(request):
    """Créances par ancienneté et saisie d'un paiement (maquette 16)."""
    _exiger(request, "ecrans.paiements")
    client = _client(request.GET.get("client"))
    return render(
        request,
        "paiements/paiements.html",
        {
            "balance": services.balance_agee(),
            "clients": Client.objects.filter(actif=True).order_by("nom"),
            "client": client,
            "ouvertes": _ouvertes(client),
            "modes": ModePaiement.choices,
            "aujourd_hui": timezone.localdate(),
            "peut_enregistrer": request.user.has_perm("paiements.enregistrer_paiement"),
        },
    )


@login_required
@require_GET
def affectations(request):
    """Factures ouvertes du client choisi (fragment rechargé par htmx)."""
    _exiger(request, "ecrans.paiements")
    client = _client(request.GET.get("client"))
    return render(
        request,
        "paiements/_affectations.html",
        {"client": client, "ouvertes": _ouvertes(client)},
    )


@login_required
@require_POST
def enregistrer(request):
    _exiger(request, "ecrans.paiements")
    client = _client(request.POST.get("client"))
    if client is None:
        messages.error(request, "Choisir le client.")
        return redirect("ecran_paiements")
    factures = {facture.pk: facture for facture in Facture.objects.filter(client=client)}
    affectations = {}
    for cle, valeur in request.POST.items():
        if cle.startswith("affecte-") and valeur.strip():
            identifiant = cle.removeprefix("affecte-")
            facture = factures.get(int(identifiant)) if identifiant.isdigit() else None
            if facture is None:
                messages.error(request, "Facture introuvable.")
                return redirect(f"{reverse('ecran_paiements')}?client={client.pk}")
            affectations[facture] = valeur
    try:
        date = datetime.date.fromisoformat(request.POST.get("date") or "")
    except ValueError:
        date = None
    try:
        services.enregistrer_paiement(
            client=client,
            montant=request.POST.get("montant", ""),
            date=date,
            mode=request.POST.get("mode", ""),
            reference=request.POST.get("reference", ""),
            justificatif=request.FILES.get("justificatif"),
            affectations=affectations,
        )
    except services.ActionImpossible as erreur:
        messages.error(request, str(erreur))
        return redirect(f"{reverse('ecran_paiements')}?client={client.pk}")
    messages.success(request, f"Paiement de {client} enregistré.")
    return redirect("releve_client", client.pk)


@login_required
def releve(request, pk):
    """Relevé de compte du client (§15) : documents émis, règlements, avance, solde."""
    _exiger(request, "paiements.voir_paiements")
    client = get_object_or_404(Client, pk=pk)
    aujourd_hui = timezone.localdate()
    documents = list(
        services.factures_avec_solde().filter(client=client).order_by("date_document", "pk")
    )
    for facture in documents:
        facture.statut_paiement = services.StatutPaiement(
            services.statut_paiement(facture, aujourd_hui, reste=facture.solde)
        )
    paiements = list(
        Paiement.objects.filter(client=client)
        .select_related("justificatif", "annule_par")
        .prefetch_related("affectations__facture")
        .order_by("date", "pk")
    )
    for paiement in paiements:
        paiement.disponible = services.disponible(paiement)
        paiement.lien = (
            url_signee(paiement.justificatif, request.user) if paiement.justificatif else None
        )
    utilisateur = request.user
    return render(
        request,
        "paiements/releve.html",
        {
            "client": client,
            "documents": documents,
            "paiements": paiements,
            "total_du": sum(facture.solde for facture in documents),
            "avance": sum(paiement.disponible for paiement in paiements),
            "ouvertes": [facture for facture in documents if facture.solde > 0],
            "peut_affecter": utilisateur.has_perm("paiements.affecter_paiement", client),
            "peut_annuler": utilisateur.has_perm("paiements.annuler_paiement", client),
        },
    )


def _action(request, pk, fonction, succes):
    _exiger(request, "paiements.voir_paiements")
    paiement = get_object_or_404(Paiement, pk=pk)
    try:
        fonction(paiement)
    except services.ActionImpossible as erreur:
        messages.error(request, str(erreur))
    else:
        messages.success(request, succes)
    return redirect("releve_client", paiement.client_id)


@login_required
@require_POST
def affecter(request, pk):
    """Affecter l'avance d'un paiement à une facture ouverte du client."""

    def fonction(paiement):
        facture = Facture.objects.filter(
            pk=request.POST.get("facture") or 0, client=paiement.client
        ).first()
        if facture is None:
            raise services.ActionImpossible("Choisir la facture.")
        services.affecter_paiement(paiement, facture, request.POST.get("montant", ""))

    return _action(request, pk, fonction, "Avance affectée.")


@login_required
@require_POST
def annuler(request, pk):
    return _action(
        request,
        pk,
        lambda paiement: services.annuler_paiement(paiement, request.POST.get("motif", "")),
        "Paiement annulé : les soldes des factures sont rétablis.",
    )
