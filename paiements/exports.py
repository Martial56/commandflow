"""Exports des créances et des paiements (§15 « exporter les états de créances et de
paiements », §16.2) : Excel et PDF, relevé de compte du client en PDF."""

import datetime

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404
from django.utils import timezone

from clients.models import Client
from noyau.exports import reponse_excel, reponse_pdf

from . import services
from .models import Paiement
from .views import contexte_releve


def _exiger(request, permission="paiements.voir_paiements"):
    if not request.user.has_perm(permission):
        raise PermissionDenied


def _entete(request):
    return {
        "entreprise": request.user.entreprise,
        "edite_le": timezone.localtime(),
        "edite_par": request.user,
    }


def factures_ouvertes_detaillees():
    """Factures restant dues, avec retard et statut de paiement, par client puis échéance."""
    aujourd_hui = timezone.localdate()
    factures = list(
        services.factures_avec_solde()
        .filter(solde__gt=0)
        .select_related("client")
        .order_by("client__nom", "echeance", "pk")
    )
    for facture in factures:
        facture.retard = max((aujourd_hui - facture.echeance).days, 0)
        facture.statut_paiement = services.StatutPaiement(
            services.statut_paiement(facture, aujourd_hui, reste=facture.solde)
        )
    return factures


def _periode(request):
    """Période des paiements : `du` / `au`, par défaut le mois en cours."""
    aujourd_hui = timezone.localdate()
    try:
        du = datetime.date.fromisoformat(request.GET.get("du") or "")
    except ValueError:
        du = aujourd_hui.replace(day=1)
    try:
        au = datetime.date.fromisoformat(request.GET.get("au") or "")
    except ValueError:
        au = aujourd_hui
    return du, au


@login_required
def creances_excel(request):
    _exiger(request)
    balance = services.balance_agee()
    factures = factures_ouvertes_detaillees()
    return reponse_excel(
        "creances",
        [
            (
                "Créances par client",
                ["Client", "Non échu", "1–30 j", "+30 j", "Total"],
                [
                    (client.nom, c["non_echu"], c["j30"], c["plus30"], c["total"])
                    for client, c in balance
                ],
                {"Non échu", "1–30 j", "+30 j", "Total"},
            ),
            (
                "Factures ouvertes",
                [
                    "Document",
                    "Client",
                    "Date",
                    "Échéance",
                    "Montant TTC",
                    "Réglé",
                    "Solde",
                    "Retard (jours)",
                    "Statut",
                    "À vérifier",
                ],
                [
                    (
                        f.numero_interne,
                        f.client.nom,
                        f.date_document,
                        f.echeance,
                        f.montant_ttc,
                        f.regle,
                        f.solde,
                        f.retard,
                        f.statut_paiement.label,
                        f.motif_verification if f.a_verifier else "",
                    )
                    for f in factures
                ],
                {"Montant TTC", "Réglé", "Solde"},
            ),
        ],
    )


@login_required
def creances_pdf(request):
    _exiger(request)
    balance = services.balance_agee()
    return reponse_pdf(
        "creances",
        "pdf/creances.html",
        {
            **_entete(request),
            "balance": balance,
            "total": {
                cle: sum(ligne[cle] for _client, ligne in balance)
                for cle in ("non_echu", "j30", "plus30", "total")
            },
            "factures": factures_ouvertes_detaillees(),
        },
        request,
    )


@login_required
def paiements_excel(request):
    """Paiements de la période (par défaut le mois en cours), annulés compris et signalés."""
    _exiger(request)
    du, au = _periode(request)
    paiements = (
        Paiement.objects.filter(date__range=(du, au))
        .select_related("client", "cree_par")
        .prefetch_related("affectations__facture")
        .order_by("date", "pk")
    )
    lignes = []
    for paiement in paiements:
        affecte = sum(a.montant for a in paiement.affectations.all())
        lignes.append(
            (
                paiement.date,
                paiement.client.nom,
                paiement.get_mode_display(),
                paiement.reference,
                paiement.montant,
                affecte,
                services.disponible(paiement),
                ", ".join(a.facture.numero_interne for a in paiement.affectations.all()),
                "oui" if paiement.annule else "",
                paiement.motif_annulation,
                str(paiement.cree_par or ""),
            )
        )
    return reponse_excel(
        f"paiements-{du:%Y%m%d}-{au:%Y%m%d}",
        [
            (
                "Paiements",
                [
                    "Date",
                    "Client",
                    "Mode",
                    "Référence",
                    "Montant",
                    "Affecté",
                    "Avance",
                    "Factures",
                    "Annulé",
                    "Motif d'annulation",
                    "Saisi par",
                ],
                lignes,
                {"Montant", "Affecté", "Avance"},
            )
        ],
    )


@login_required
def releve_pdf(request, pk):
    _exiger(request)
    client = get_object_or_404(Client, pk=pk)
    return reponse_pdf(
        f"releve-{client.code}",
        "pdf/releve.html",
        {**_entete(request), **contexte_releve(client, request.user)},
        request,
    )
