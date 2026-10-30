"""Réception du chargement par le livreur, sur téléphone (maquette 4).

Le chauffeur (ou le compte du prestataire) confirme lui-même chaque commande reçue, ou
signale un écart, puis démarre la tournée (§10). Pour un prestataire sans compte, le
gestionnaire saisit à sa place depuis l'écran Tournées : sa saisie attend la validation
d'un autre responsable (décision 8.3). Un livreur ne voit jamais la tournée d'un autre
(§22-25).
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from noyau.models import Validation

from . import services
from .etat import exceptions, recapitulatif
from .models import Chargement, LigneChargement, StatutChargement, StatutTournee, Tournee
from .services import STATUTS_MODIFIABLES


def _tournee(request, pk):
    tournee = get_object_or_404(
        Tournee.objects.select_related("chauffeur", "prestataire__compte", "vehicule"), pk=pk
    )
    if not request.user.has_perm("logistique.recevoir_chargement", tournee):
        raise PermissionDenied
    return tournee


def _contexte(request, tournee):
    lignes = LigneChargement.objects.select_related("ligne_commande__produit").order_by("pk")
    chargements = list(
        Chargement.objects.filter(tournee=tournee)
        .exclude(statut=StatutChargement.RETIRE)
        .select_related("commande__client", "commande__adresse", "verifie_par", "recu_par")
        .prefetch_related(Prefetch("lignes", queryset=lignes))
        .order_by("ordre_passage", "pk")
    )
    for chargement in chargements:
        retenues = chargement.quantites_retenues()
        chargement.lignes_recues = [
            (ligne, retenues[ligne.pk]) for ligne in chargement.lignes.all()
        ]
        chargement.ecarts = [ligne for ligne in chargement.lignes.all() if ligne.ecart]
        chargement.reception_en_attente = exceptions(
            chargement, "reception", Validation.Decision.EN_ATTENTE
        ).exists()
    recap = recapitulatif(tournee)
    return {
        "tournee": tournee,
        "chargements": chargements,
        "recap": recap,
        "restantes": recap["attendues"] - recap["recues"],
        "peut_demarrer": tournee.statut == StatutTournee.DEPART_AUTORISE,
        "pour_le_prestataire": tournee.compte_livreur is None,
    }


def _afficher(request, tournee):
    contexte = _contexte(request, tournee)
    if request.headers.get("HX-Request"):
        return render(request, "logistique/_reception.html", contexte)
    return render(request, "logistique/reception.html", contexte)


@login_required
def ma_tournee(request):
    """Écran d'arrivée du livreur : sa prochaine tournée (pas encore terminée)."""
    if not request.user.has_perm("ecrans.ma_tournee"):
        raise PermissionDenied
    tournee = (
        Tournee.objects.filter(
            Q(chauffeur=request.user) | Q(prestataire__compte=request.user),
            date__gte=timezone.localdate(),
            statut__in=[*STATUTS_MODIFIABLES, StatutTournee.EN_COURS],
        )
        .select_related("chauffeur", "prestataire__compte", "vehicule")
        .order_by("date", "numero")
        .first()
    )
    if tournee is None:
        return render(request, "logistique/reception.html", {"tournee": None})
    return _afficher(request, _tournee(request, tournee.pk))


@login_required
def reception(request, pk):
    """Une tournée précise : le livreur affecté, ou le gestionnaire pour un prestataire
    sans compte."""
    return _afficher(request, _tournee(request, pk))


@login_required
@require_POST
def recevoir(request, pk):
    """Confirmer la réception d'une commande, ou signaler un écart (avec motif)."""
    chargement = get_object_or_404(Chargement.objects.select_related("tournee"), pk=pk)
    tournee = _tournee(request, chargement.tournee_id)
    quantites = {
        ligne.pk: request.POST.get(f"recu-{ligne.pk}", "").strip().replace(",", ".")
        for ligne in chargement.lignes.all()
    }
    try:
        if request.POST.get("action") == "ecart":
            services.contester_reception(chargement, quantites, request.POST.get("motif", ""))
            messages.success(request, "Écart signalé : un responsable doit le valider.")
        else:
            chargement = services.confirmer_reception(chargement, quantites)
            if chargement.statut == StatutChargement.RECUE_CHAUFFEUR:
                messages.success(request, f"{chargement.commande} reçue.")
            else:
                messages.success(
                    request, "Réception enregistrée : un autre responsable doit la valider."
                )
    except services.ActionImpossible as erreur:
        messages.error(request, str(erreur))
    return redirect("reception_tournee", tournee.pk)


@login_required
@require_POST
def demarrer(request, pk):
    tournee = _tournee(request, pk)
    try:
        services.demarrer_tournee(tournee)
    except services.ActionImpossible as erreur:
        messages.error(request, str(erreur))
    else:
        messages.success(request, "Tournée démarrée : bonne route !")
    return redirect("reception_tournee", tournee.pk)
