"""Écran Tournées (maquette 14) : commandes à affecter, tournées du jour, décisions.

Le tableau se recharge toutes les 30 secondes (décision 8.3) : la requête htmx reçoit
le fragment `_tableau.html`. Chaque action appelle un service de `logistique.services`.
"""

import datetime
from collections import Counter, defaultdict

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.db.models import Prefetch, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from commandes.models import Commande
from comptes.models import Role, Utilisateur
from noyau.contexte import entreprise_obligatoire
from noyau.models import Validation
from noyau.services import ValidationPropreDemande

from . import services
from .etat import recapitulatif
from .models import (
    Chargement,
    LigneChargement,
    Prestataire,
    StatutChargement,
    Tournee,
    Vehicule,
)
from .services import STATUTS_MODIFIABLES


def _exiger(request, permission, objet=None):
    if not request.user.has_perm(permission, objet):
        raise PermissionDenied


def _date(valeur):
    try:
        return datetime.date.fromisoformat(valeur or "")
    except ValueError:
        return timezone.localdate()


def _retour(date):
    return redirect(f"{reverse('ecran_tournees')}?date={date.isoformat()}")


def _executer(request, date, action, succes):
    """Appelle un service ; l'erreur métier s'affiche en message, sans changement."""
    try:
        action()
    except services.ActionImpossible as erreur:
        messages.error(request, str(erreur))
    else:
        messages.success(request, succes)
    return _retour(date)


# --- Lecture ----------------------------------------------------------------------------


def _chauffeurs():
    return Utilisateur.objects.filter(
        entreprise=entreprise_obligatoire(),
        is_active=True,
        groups__name=Role.CHAUFFEUR.label,
    ).order_by("first_name", "username")


def _demandes(chargements):
    """Demandes d'exception en attente, par chargement."""
    demandes = defaultdict(list)
    for demande in Validation.objects.filter(
        content_type=ContentType.objects.get_for_model(Chargement),
        object_id__in=[chargement.pk for chargement in chargements],
        action=Validation.Action.EXCEPTION,
        decision=Validation.Decision.EN_ATTENTE,
    ).select_related("demandee_par"):
        demandes[demande.object_id].append(demande)
    return demandes


def _tournees(date, utilisateur):
    lignes = LigneChargement.objects.select_related("ligne_commande__produit").order_by("pk")
    tournees = list(
        Tournee.objects.filter(date=date)
        .select_related("chauffeur", "prestataire__compte", "vehicule")
        .prefetch_related(
            Prefetch(
                "chargements",
                queryset=Chargement.objects.exclude(statut=StatutChargement.RETIRE)
                .select_related("commande__client", "commande__adresse")
                .prefetch_related(Prefetch("lignes", queryset=lignes)),
                to_attr="actifs",
            )
        )
        .order_by("numero")
    )
    demandes = _demandes([c for tournee in tournees for c in tournee.actifs])
    for tournee in tournees:
        communes = Counter(c.commande.adresse.commune for c in tournee.actifs)
        tournee.commune = communes.most_common(1)[0][0] if communes else ""
        tournee.recap = recapitulatif(tournee)
        tournee.modifiable = tournee.statut in STATUTS_MODIFIABLES
        tournee.peut_gerer = utilisateur.has_perm("logistique.gerer_tournee", tournee)
        for chargement in tournee.actifs:
            chargement.demandes = demandes.get(chargement.pk, [])
            chargement.ecarts = [ligne for ligne in chargement.lignes.all() if ligne.ecart]
            chargement.peut_exception = (
                tournee.modifiable
                and chargement.statut != StatutChargement.RECUE_CHAUFFEUR
                and not any(d.contenu.get("objet") == "depart" for d in chargement.demandes)
                and utilisateur.has_perm("logistique.demander_exception_depart", chargement)
            )
            chargement.peut_decider = utilisateur.has_perm(
                "logistique.valider_exception", chargement
            )
    return tournees


@login_required
def tableau(request):
    """Tournées d'une date (maquette 14), rafraîchies toutes les 30 secondes."""
    _exiger(request, "ecrans.tournees")
    date = _date(request.GET.get("date"))
    affectables = services.commandes_affectables(date).annotate(
        pieces=Sum("lignes__quantite", filter=Q(lignes__retiree=False))
    )
    tournees = _tournees(date, request.user)
    contexte = {
        "date": date,
        "affectables": affectables,
        "tournees": tournees,
        "destinations": [t for t in tournees if t.modifiable and t.peut_gerer],
        "peut_creer": request.user.has_perm("logistique.gerer_tournees"),
        "chauffeurs": _chauffeurs(),
        "prestataires": Prestataire.objects.filter(actif=True).order_by("nom"),
        "vehicules": Vehicule.objects.filter(actif=True).order_by("immatriculation"),
        "heure": timezone.localtime(),
    }
    if request.headers.get("HX-Request"):
        return render(request, "logistique/_tableau.html", contexte)
    return render(request, "logistique/tournees.html", contexte)


# --- Actions ----------------------------------------------------------------------------


def _livreur(request):
    """Champ « livreur » : `c-<pk>` pour un chauffeur, `p-<pk>` pour un prestataire."""
    valeur = request.POST.get("livreur", "")
    genre, _, pk = valeur.partition("-")
    if genre == "c" and pk.isdigit():
        return {"chauffeur": get_object_or_404(_chauffeurs(), pk=pk)}
    if genre == "p" and pk.isdigit():
        return {"prestataire": get_object_or_404(Prestataire, pk=pk, actif=True)}
    return {}


def _vehicule(request):
    pk = request.POST.get("vehicule", "")
    return get_object_or_404(Vehicule, pk=pk) if pk.isdigit() else None


@login_required
@require_POST
def creer(request):
    _exiger(request, "ecrans.tournees")
    date = _date(request.POST.get("date"))
    livreur, vehicule = _livreur(request), _vehicule(request)
    return _executer(
        request,
        date,
        lambda: services.creer_tournee(date=date, vehicule=vehicule, **livreur),
        "Tournée créée.",
    )


@login_required
@require_POST
def changer_livreur(request, pk):
    _exiger(request, "ecrans.tournees")
    tournee = get_object_or_404(Tournee, pk=pk)
    livreur, vehicule = _livreur(request), _vehicule(request)
    return _executer(
        request,
        tournee.date,
        lambda: services.changer_livreur(tournee, vehicule=vehicule, **livreur),
        f"{tournee} : livreur mis à jour.",
    )


@login_required
@require_POST
def affecter(request):
    _exiger(request, "ecrans.tournees")
    tournee = get_object_or_404(Tournee, pk=request.POST.get("tournee") or 0)
    commande = get_object_or_404(Commande, pk=request.POST.get("commande") or 0)
    return _executer(
        request,
        tournee.date,
        lambda: services.affecter_commande(tournee, commande),
        f"{commande} affectée à {tournee}.",
    )


@login_required
@require_POST
def retirer(request, pk):
    _exiger(request, "ecrans.tournees")
    chargement = get_object_or_404(Chargement.objects.select_related("tournee"), pk=pk)
    return _executer(
        request,
        chargement.tournee.date,
        lambda: services.retirer_commande(chargement),
        f"{chargement.commande} retirée de {chargement.tournee}.",
    )


@login_required
@require_POST
def deplacer(request, pk):
    """Monte ou descend un arrêt d'un rang."""
    _exiger(request, "ecrans.tournees")
    chargement = get_object_or_404(Chargement.objects.select_related("tournee"), pk=pk)
    tournee = chargement.tournee
    ordre = list(tournee.chargements_actifs().order_by("ordre_passage", "pk"))
    rang = ordre.index(chargement) if chargement in ordre else -1
    voisin = rang + (-1 if request.POST.get("sens") == "haut" else 1)
    if rang >= 0 and 0 <= voisin < len(ordre):
        ordre[rang], ordre[voisin] = ordre[voisin], ordre[rang]
    return _executer(
        request, tournee.date, lambda: services.reordonner(tournee, ordre), "Ordre mis à jour."
    )


@login_required
@require_POST
def proposer_ordre(request, pk):
    _exiger(request, "ecrans.tournees")
    tournee = get_object_or_404(Tournee, pk=pk)
    return _executer(
        request,
        tournee.date,
        lambda: services.proposer_ordre(tournee),
        f"{tournee} : ordre proposé par créneau puis par commune.",
    )


@login_required
@require_POST
def exception_depart(request, pk):
    _exiger(request, "ecrans.tournees")
    chargement = get_object_or_404(Chargement.objects.select_related("tournee"), pk=pk)
    return _executer(
        request,
        chargement.tournee.date,
        lambda: services.demander_exception_depart(chargement, request.POST.get("motif", "")),
        "Exception de départ demandée : un autre responsable doit la valider.",
    )


@login_required
@require_POST
def decider(request, pk):
    """Accepter ou refuser une demande d'exception (écart, départ, réception)."""
    _exiger(request, "ecrans.tournees")
    demande = get_object_or_404(
        Validation,
        pk=pk,
        content_type=ContentType.objects.get_for_model(Chargement),
        decision=Validation.Decision.EN_ATTENTE,
    )
    accepter = request.POST.get("decision") == "accepter"
    date = Chargement.objects.select_related("tournee").get(pk=demande.object_id).tournee.date
    try:
        services.decider_exception_chargement(demande, accepter=accepter)
    except (services.ActionImpossible, ValidationPropreDemande) as erreur:
        messages.error(request, str(erreur))
    else:
        messages.success(request, "Demande acceptée." if accepter else "Demande refusée.")
    return _retour(date)
