"""Contrôle du chargement par le préparateur, sur téléphone (maquette 3).

Chaque produit compté est enregistré aussitôt (htmx) ; le préparateur valide ensuite le
chargement, ou signale l'écart au gestionnaire. Il ne peut jamais confirmer la réception :
le chauffeur la confirme lui-même depuis son compte (§10).
"""

import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from noyau.models import Validation

from . import services
from .etat import exceptions, recapitulatif
from .models import Chargement, LigneChargement, MotifEcart, StatutChargement, Tournee
from .services import STATUTS_MODIFIABLES


def _exiger(request):
    if not request.user.has_perm("logistique.acceder_controle"):
        raise PermissionDenied


def _chargement(pk):
    return get_object_or_404(
        Chargement.objects.select_related(
            "tournee__chauffeur", "tournee__prestataire", "commande__client", "commande__adresse"
        ),
        pk=pk,
    )


def _date_par_defaut():
    """Première date, à partir d'aujourd'hui, ayant une tournée pas encore partie."""
    tournee = (
        Tournee.objects.filter(statut__in=STATUTS_MODIFIABLES, date__gte=timezone.localdate())
        .order_by("date")
        .first()
    )
    return tournee.date if tournee else timezone.localdate()


@login_required
def liste(request):
    """Tournées à préparer et leurs arrêts, dans l'ordre de passage (Préparation, §3.1).

    Le Gestionnaire ouvre un arrêt depuis l'écran Tournées.
    """
    if not request.user.has_perm("ecrans.chargement"):
        raise PermissionDenied
    try:
        date = datetime.date.fromisoformat(request.GET.get("date", ""))
    except ValueError:
        date = _date_par_defaut()
    tournees = list(
        Tournee.objects.filter(date=date, statut__in=STATUTS_MODIFIABLES)
        .select_related("chauffeur", "prestataire")
        .prefetch_related(
            Prefetch(
                "chargements",
                queryset=Chargement.objects.exclude(statut=StatutChargement.RETIRE)
                .select_related("commande__client", "commande__adresse")
                .order_by("ordre_passage", "pk"),
                to_attr="actifs",
            )
        )
        .order_by("numero")
    )
    for tournee in tournees:
        tournee.recap = recapitulatif(tournee)
    return render(request, "logistique/chargement_liste.html", {"date": date, "tournees": tournees})


def _liste(request, tournee):
    """Retour à la liste des arrêts (Préparation) ou à l'écran Tournées (Gestionnaire)."""
    nom = "ecran_chargement" if request.user.has_perm("ecrans.chargement") else "ecran_tournees"
    return f"{reverse(nom)}?date={tournee.date.isoformat()}"


def _contexte(chargement, erreur=""):
    lignes = list(
        LigneChargement.objects.filter(chargement=chargement)
        .select_related("ligne_commande__produit")
        .order_by("pk")
    )
    suivant = (
        chargement.tournee.chargements_actifs()
        .filter(ordre_passage__gt=chargement.ordre_passage)
        .order_by("ordre_passage", "pk")
        .first()
    )
    comptees = [ligne for ligne in lignes if ligne.quantite_preparee is not None]
    return {
        "chargement": chargement,
        "tournee": chargement.tournee,
        "lignes": lignes,
        "motifs": MotifEcart.choices,
        "tout_compte": len(comptees) == len(lignes),
        "tout_conforme": bool(lignes) and all(ligne.conforme for ligne in lignes),
        "demande_en_attente": exceptions(
            chargement, "ecart", Validation.Decision.EN_ATTENTE
        ).exists(),
        "suivant": suivant,
        "erreur": erreur,
    }


@login_required
def controle(request, pk):
    """Un arrêt : comptage produit par produit (maquette 3)."""
    _exiger(request)
    chargement = _chargement(pk)
    contexte = {**_contexte(chargement), "retour": _liste(request, chargement.tournee)}
    return render(request, "logistique/chargement_controle.html", contexte)


@login_required
@require_POST
def compter(request, pk):
    """Une ligne comptée (htmx) : renvoie la zone de contrôle recalculée."""
    _exiger(request)
    ligne = get_object_or_404(LigneChargement.objects.select_related("chargement"), pk=pk)
    erreur = ""
    try:
        services.compter_ligne(
            ligne,
            request.POST.get("quantite", "").strip().replace(",", "."),
            motif=request.POST.get("motif", ""),
            detail=request.POST.get("detail", ""),
        )
    except services.ActionImpossible as exc:
        erreur = str(exc)
    contexte = _contexte(_chargement(ligne.chargement_id), erreur)
    if request.headers.get("HX-Request"):
        return render(request, "logistique/_controle.html", contexte)
    if erreur:
        messages.error(request, erreur)
    return redirect("chargement_controle", ligne.chargement_id)


def _action(request, pk, fonction, succes, vers_suivant=False):
    _exiger(request)
    chargement = _chargement(pk)
    try:
        fonction(chargement)
    except services.ActionImpossible as erreur:
        messages.error(request, str(erreur))
        return redirect("chargement_controle", pk)
    messages.success(request, succes)
    suivant = _contexte(chargement)["suivant"] if vers_suivant else None
    if suivant is not None:
        return redirect("chargement_controle", suivant.pk)
    if vers_suivant:
        return redirect(_liste(request, chargement.tournee))
    return redirect("chargement_controle", pk)


@login_required
@require_POST
def valider(request, pk):
    return _action(
        request,
        pk,
        services.valider_preparation,
        "Chargement vérifié : le chauffeur doit maintenant confirmer la réception.",
        vers_suivant=True,
    )


@login_required
@require_POST
def signaler(request, pk):
    commentaire = request.POST.get("commentaire", "")
    return _action(
        request,
        pk,
        lambda chargement: services.signaler_ecart(chargement, commentaire),
        "Écart signalé au gestionnaire.",
        vers_suivant=True,
    )


@login_required
@require_POST
def completer(request, pk):
    return _action(
        request,
        pk,
        services.completer_chargement,
        "Marchandise complétée : recompter les produits.",
    )
