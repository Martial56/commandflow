"""Écran Facturation (maquette 15) : livraisons à facturer, choix FNE ou autre document,
documents émis et leur recherche (§14, §22-23 ; décisions 8.5).

Chaque action appelle un service de `facturation.services` ; les droits sont vérifiés
côté serveur (`facturation/rules.py`).
"""

import datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from noyau.fichiers import url_signee

from . import services
from .models import (
    LIEN_PORTAIL_FNE,
    Facture,
    NatureAutreDocument,
    StatutFacture,
    TypeDocument,
)

ETATS = [
    ("", "Tous"),
    ("emise", "Émis"),
    ("manquant", "Document manquant"),
    ("a_verifier", "Contestés / à vérifier"),
    ("annulee", "Annulés"),
]


def _exiger(request, permission, objet=None):
    if not request.user.has_perm(permission, objet):
        raise PermissionDenied


def _date(valeur):
    try:
        return datetime.date.fromisoformat(valeur or "")
    except ValueError:
        return None


def _montant(valeur):
    try:
        return Decimal((valeur or "").replace(" ", "").replace(" ", ""))
    except InvalidOperation:
        return None


# --- Lecture ---------------------------------------------------------------------------


def _a_facturer():
    """Livraisons à facturer, avec leur montant livré et l'échéance par défaut."""
    aujourd_hui = timezone.localdate()
    lignes = list(services.livraisons_a_facturer())
    echeances = {}
    for livraison in lignes:
        client = livraison.commande.client
        livraison.montant = services.montants_livraison(livraison)[1]
        if client.pk not in echeances:
            echeances[client.pk] = services.echeance_par_defaut(client, aujourd_hui)
        livraison.echeance = echeances[client.pk]
    return lignes


def _documents(parametres):
    """Recherche par client, numéro, date, montant et état (§14, §22-23)."""
    documents = Facture.objects.select_related("client", "piece")
    texte = (parametres.get("q") or "").strip()
    if texte:
        documents = documents.filter(
            Q(client__nom__icontains=texte)
            | Q(numero_interne__icontains=texte)
            | Q(numero_fne__icontains=texte)
        )
    if du := _date(parametres.get("du")):
        documents = documents.filter(date_document__gte=du)
    if au := _date(parametres.get("au")):
        documents = documents.filter(date_document__lte=au)
    if (montant := _montant(parametres.get("montant"))) is not None:
        documents = documents.filter(montant_ttc=montant)
    etat = parametres.get("etat", "")
    if etat == "emise":
        documents = documents.filter(statut=StatutFacture.EMISE)
    elif etat == "annulee":
        documents = documents.filter(statut=StatutFacture.ANNULEE)
    elif etat == "manquant":
        documents = documents.filter(statut=StatutFacture.EMISE, piece__isnull=True)
    elif etat == "a_verifier":
        documents = documents.filter(a_verifier=True)
    return documents.order_by("-date_document", "-pk")[:200]


@login_required
def ecran(request):
    """Onglets « À facturer » et « Documents émis » (maquette 15)."""
    _exiger(request, "ecrans.facturation")
    vue = "emis" if request.GET.get("vue") == "emis" else "a_facturer"
    a_facturer = _a_facturer()
    contexte = {
        "vue": vue,
        "a_facturer": a_facturer,
        "peut_emettre": request.user.has_perm("facturation.emettre_facture"),
        "natures": NatureAutreDocument.choices,
        "type_fne": TypeDocument.FNE,
        "portail_fne": LIEN_PORTAIL_FNE,
        "etats": ETATS,
        "recherche": request.GET,
    }
    if vue == "emis":
        contexte["documents"] = _documents(request.GET)
        if request.headers.get("HX-Request"):
            return render(request, "facturation/_documents.html", contexte)
    return render(request, "facturation/facturation.html", contexte)


@login_required
def documents_excel(request):
    """Documents émis de la recherche courante (§14, §16.2), avec solde et statut de
    paiement."""
    from noyau.exports import reponse_excel
    from paiements.services import StatutPaiement, solde, statut_paiement

    _exiger(request, "facturation.voir_factures")
    lignes = []
    for facture in _documents(request.GET):
        reste = solde(facture)
        emise = facture.statut == StatutFacture.EMISE
        lignes.append(
            (
                facture.numero_interne,
                facture.date_document,
                facture.client.nom,
                "FNE" if facture.est_fne else facture.get_nature_display(),
                facture.numero_fne,
                facture.montant_ht,
                facture.montant_ttc,
                facture.echeance,
                reste,
                StatutPaiement(statut_paiement(facture, reste=reste)).label if emise else "",
                facture.get_statut_display(),
                "oui" if emise and facture.document_manquant else "",
                facture.motif_verification if facture.a_verifier else "",
            )
        )
    return reponse_excel(
        "documents",
        [
            (
                "Documents émis",
                [
                    "Document",
                    "Date",
                    "Client",
                    "Type",
                    "N° FNE",
                    "Montant HT",
                    "Montant TTC",
                    "Échéance",
                    "Solde",
                    "Paiement",
                    "État",
                    "Document manquant",
                    "À vérifier",
                ],
                lignes,
                {"Montant HT", "Montant TTC", "Solde"},
            )
        ],
    )


@login_required
def document(request, pk):
    """Fiche d'un document : livraisons couvertes, PDF, annulation, vérification."""
    _exiger(request, "facturation.voir_factures")
    facture = get_object_or_404(
        Facture.objects.select_related("client", "piece", "choix_fait_par"), pk=pk
    )
    lignes = facture.lignes.select_related("livraison__commande", "livraison__chargement")
    utilisateur = request.user
    emise = facture.statut == StatutFacture.EMISE
    return render(
        request,
        "facturation/document.html",
        {
            "facture": facture,
            "lignes": lignes,
            "lien_document": url_signee(facture.piece, utilisateur) if facture.piece else None,
            "portail_fne": LIEN_PORTAIL_FNE,
            "peut_joindre": emise
            and utilisateur.has_perm("facturation.completer_facture", facture),
            "peut_annuler": emise and utilisateur.has_perm("facturation.annuler_facture", facture),
            "peut_verifier": utilisateur.has_perm("facturation.marquer_a_verifier", facture),
        },
    )


# --- Actions ---------------------------------------------------------------------------


@login_required
@require_POST
def emettre(request):
    """Enregistrer le document pour la sélection (choix FNE ou autre document)."""
    _exiger(request, "ecrans.facturation")
    try:
        facture = services.enregistrer_choix(
            request.POST.getlist("livraisons"),
            type_document=request.POST.get("type_document", ""),
            numero_fne=request.POST.get("numero_fne", ""),
            nature=request.POST.get("nature", ""),
            motif=request.POST.get("motif", ""),
            fichier=request.FILES.get("fichier"),
            echeance=_date(request.POST.get("echeance")),
        )
    except services.ActionImpossible as erreur:
        messages.error(request, str(erreur))
        return redirect("ecran_facturation")
    messages.success(request, f"Document {facture} enregistré pour {facture.client}.")
    return redirect("facture_detail", facture.pk)


def _action(request, pk, fonction, succes):
    _exiger(request, "facturation.voir_factures")
    facture = get_object_or_404(Facture, pk=pk)
    try:
        fonction(facture)
    except services.ActionImpossible as erreur:
        messages.error(request, str(erreur))
    else:
        messages.success(request, succes)
    return redirect("facture_detail", pk)


@login_required
@require_POST
def joindre(request, pk):
    return _action(
        request,
        pk,
        lambda facture: services.joindre_document(facture, request.FILES.get("fichier")),
        "Document joint.",
    )


@login_required
@require_POST
def annuler(request, pk):
    return _action(
        request,
        pk,
        lambda facture: services.annuler_facture(facture, request.POST.get("motif", "")),
        "Document annulé : ses livraisons sont de nouveau à facturer.",
    )


@login_required
@require_POST
def verifier(request, pk):
    if request.POST.get("action") == "lever":
        return _action(request, pk, services.lever_verification, "Vérification levée.")
    return _action(
        request,
        pk,
        lambda facture: services.marquer_a_verifier(facture, request.POST.get("motif", "")),
        "Document marqué « contesté / à vérifier ».",
    )
