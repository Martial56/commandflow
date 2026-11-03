"""Écran Tournées (maquette 14) : commandes à affecter, tournées du jour, décisions.

Le tableau se recharge toutes les 30 secondes (décision 8.3) : la requête htmx reçoit
le fragment `_tableau.html`. Chaque action appelle un service de `logistique.services`.
"""

import datetime
from collections import Counter, defaultdict
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Prefetch, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from catalogue.models import Produit
from commandes import services as commandes
from commandes.models import Commande, LigneCommande, Statut
from comptes.models import Role, Utilisateur
from noyau.contexte import entreprise_obligatoire
from noyau.models import Validation
from noyau.services import ValidationPropreDemande

from . import services
from .etat import recapitulatif
from .models import (
    Chargement,
    DecisionRetour,
    EtatRetour,
    Incident,
    LigneChargement,
    LigneLivraison,
    Livraison,
    Prestataire,
    Reliquat,
    Retour,
    StatutChargement,
    StatutLivraison,
    StatutTournee,
    Tournee,
    Vehicule,
)
from .services import STATUTS_LIVRAISON_TRAITEE, STATUTS_MODIFIABLES, arrets_traites


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


def _retours(tournees):
    """Retours déclarés et produits retournables (livraisons traitées), par tournée."""
    retours, retournables = defaultdict(list), defaultdict(list)
    for retour in (
        Retour.objects.filter(livraison__chargement__tournee__in=tournees)
        .select_related("produit", "livraison__chargement", "livraison__commande__client")
        .select_related("cree_par", "decide_par")
        .order_by("pk")
    ):
        retours[retour.livraison.chargement.tournee_id].append(retour)
    for ligne in (
        LigneLivraison.objects.filter(
            livraison__chargement__tournee__in=tournees,
            livraison__statut__in=STATUTS_LIVRAISON_TRAITEE,
        )
        .select_related("livraison__chargement", "livraison__commande__client")
        .select_related("ligne_commande__produit")
        .order_by("livraison__chargement__ordre_passage", "pk")
    ):
        retournables[ligne.livraison.chargement.tournee_id].append(ligne)
    return retours, retournables


def _suivi(tournees):
    """Tournées parties : livraison de chaque arrêt, demandes en attente, incidents."""
    parties = [t for t in tournees if t.statut in (StatutTournee.EN_COURS, StatutTournee.TERMINEE)]
    livraisons = {
        livraison.chargement_id: livraison
        for livraison in Livraison.objects.filter(chargement__tournee__in=parties)
    }
    saisies = defaultdict(list)  # livraisons saisies pour un prestataire sans compte
    for demande in Validation.objects.filter(
        content_type=ContentType.objects.get_for_model(Livraison),
        object_id__in=[livraison.pk for livraison in livraisons.values()],
        action=Validation.Action.EXCEPTION,
        decision=Validation.Decision.EN_ATTENTE,
        contenu__objet="livraison",
    ).select_related("demandee_par"):
        saisies[demande.object_id].append(demande)
    abandons = defaultdict(list)  # demandes d'abandon des commandes en anomalie
    for demande in Validation.objects.filter(
        content_type=ContentType.objects.get_for_model(Commande),
        object_id__in=[livraison.commande_id for livraison in livraisons.values()],
        action=Validation.Action.ANNULATION,
        decision=Validation.Decision.EN_ATTENTE,
    ).select_related("demandee_par"):
        abandons[demande.object_id].append(demande)
    incidents = defaultdict(list)
    for incident in Incident.objects.filter(tournee__in=parties).select_related("cree_par"):
        incidents[incident.tournee_id].append(incident)
    return livraisons, saisies, abandons, incidents


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
    retours, retournables = _retours(tournees)
    livraisons, saisies, abandons, incidents = _suivi(tournees)
    for tournee in tournees:
        tournee.partie = tournee.statut in (StatutTournee.EN_COURS, StatutTournee.TERMINEE)
        tournee.liste_incidents = incidents.get(tournee.pk, [])
        tournee.traites = 0
        tournee.retours = retours.get(tournee.pk, [])
        tournee.retournables = retournables.get(tournee.pk, [])
        tournee.peut_declarer_retour = utilisateur.has_perm("logistique.declarer_retour", tournee)
        tournee.peut_decider_retour = utilisateur.has_perm("logistique.decider_retour", tournee)
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
            livraison = livraisons.get(chargement.pk)
            chargement.suivi = livraison
            if livraison is None:
                continue
            tournee.traites += livraison.statut in STATUTS_LIVRAISON_TRAITEE
            livraison.saisies = saisies.get(livraison.pk, [])
            livraison.abandons = abandons.get(livraison.commande_id, [])
            en_anomalie = chargement.commande.statut == Statut.EN_ANOMALIE
            livraison.peut_reporter = (
                livraison.statut == StatutLivraison.ECHOUEE
                and en_anomalie
                and utilisateur.has_perm("logistique.reporter_livraison", livraison)
            )
            livraison.peut_abandon = (
                en_anomalie
                and not livraison.abandons
                and utilisateur.has_perm("commandes.annuler_commande", chargement.commande)
            )
        # Tournée en cours : seuls les arrêts restants se réordonnent (décision 8.4).
        tournee.reordonnables = (
            [
                c
                for c in tournee.actifs
                if tournee.modifiable
                or (
                    tournee.statut == StatutTournee.EN_COURS
                    and c.suivi is not None
                    and c.suivi.statut not in STATUTS_LIVRAISON_TRAITEE
                )
            ]
            if tournee.peut_gerer
            else []
        )
        for rang, chargement in enumerate(tournee.reordonnables):
            chargement.peut_monter = rang > 0
            chargement.peut_descendre = rang < len(tournee.reordonnables) - 1
    return tournees


def _pieces(affectables, date):
    """Pièces à charger : la commande entière, ou son reliquat planifié ce jour-là."""
    reliquats = defaultdict(Decimal)
    for reliquat in Reliquat.objects.filter(
        commande_origine__in=affectables, traite=False, nouvelle_date=date
    ):
        reliquats[reliquat.commande_origine_id] += reliquat.quantite
    totaux = dict(
        LigneCommande.objects.filter(commande__in=affectables, retiree=False)
        .values("commande")
        .annotate(total=Sum("quantite"))
        .values_list("commande", "total")
    )
    for commande in affectables:
        commande.reliquat = commande.statut == Statut.PARTIELLEMENT_LIVREE
        commande.pieces = reliquats[commande.pk] if commande.reliquat else totaux.get(commande.pk)


def _reliquats_a_planifier(utilisateur):
    """Commandes dont le reliquat est ouvert et hors tournée (décision 8.4) : produits
    restants, date prévue, demande de solde en attente."""
    commandes = {}
    for reliquat in (
        Reliquat.objects.filter(traite=False, chargement__isnull=True)
        .select_related("commande_origine__client", "ligne_commande__produit")
        .order_by("commande_origine__numero", "pk")
    ):
        commande = commandes.setdefault(reliquat.commande_origine_id, reliquat.commande_origine)
        commande.restes = [*getattr(commande, "restes", []), reliquat]
        commande.date_prevue = reliquat.nouvelle_date
        commande.livraison_id = reliquat.livraison_id
    soldes = {
        demande.contenu.get("commande"): demande
        for demande in Validation.objects.filter(
            content_type=ContentType.objects.get_for_model(Livraison),
            action=Validation.Action.EXCEPTION,
            decision=Validation.Decision.EN_ATTENTE,
            contenu__objet="solde_reliquat",
        ).select_related("demandee_par")
    }
    peut_decider = utilisateur.has_perm("logistique.valider_exception")
    for commande in commandes.values():
        commande.solde = soldes.get(commande.pk)
        commande.peut_decider_solde = (
            commande.solde is not None
            and peut_decider
            and commande.solde.demandee_par_id != utilisateur.pk
        )
    return list(commandes.values())


@login_required
def tableau(request):
    """Tournées d'une date (maquette 14), rafraîchies toutes les 30 secondes."""
    _exiger(request, "ecrans.tournees")
    date = _date(request.GET.get("date"))
    affectables = list(services.commandes_affectables(date))
    _pieces(affectables, date)
    tournees = _tournees(date, request.user)
    contexte = {
        "date": date,
        "affectables": affectables,
        "tournees": tournees,
        "destinations": [t for t in tournees if t.modifiable and t.peut_gerer],
        "peut_creer": request.user.has_perm("logistique.gerer_tournees"),
        "peut_controler": request.user.has_perm("logistique.acceder_controle"),
        "chauffeurs": _chauffeurs(),
        "prestataires": Prestataire.objects.filter(actif=True).order_by("nom"),
        "vehicules": Vehicule.objects.filter(actif=True).order_by("immatriculation"),
        "heure": timezone.localtime(),
        "etats_retour": EtatRetour.choices,
        "decisions_retour": DecisionRetour.choices,
        "reliquats": _reliquats_a_planifier(request.user),
        "peut_planifier": request.user.has_perm("logistique.planifier_reliquat"),
        "peut_solder": request.user.has_perm("logistique.solder_reliquat"),
        "demain": timezone.localdate() + datetime.timedelta(days=1),
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
    # Tournée en cours : l'arrêt s'échange avec le restant voisin (décision 8.4).
    traites = arrets_traites(tournee) if tournee.statut == StatutTournee.EN_COURS else set()
    places = [i for i, c in enumerate(ordre) if c.pk not in traites]
    rang = (
        places.index(ordre.index(chargement))
        if chargement in ordre and (chargement.pk not in traites)
        else -1
    )
    voisin = rang + (-1 if request.POST.get("sens") == "haut" else 1)
    if rang >= 0 and 0 <= voisin < len(places):
        i, j = places[rang], places[voisin]
        ordre[i], ordre[j] = ordre[j], ordre[i]
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


# --- Retours (§12.3, décision 8.4) ----------------------------------------------------


def _entier(valeur):
    try:
        return int(valeur)
    except (TypeError, ValueError):
        return 0


@login_required
@require_POST
def declarer_retour(request, pk):
    """Retour déclaré par le gestionnaire au retour au dépôt : `ligne` = livraison-produit."""
    _exiger(request, "ecrans.tournees")
    tournee = get_object_or_404(Tournee, pk=pk)
    livraison_pk, _, produit_pk = request.POST.get("ligne", "").partition("-")
    livraison = get_object_or_404(Livraison, pk=_entier(livraison_pk), chargement__tournee=tournee)
    produit = get_object_or_404(Produit, pk=_entier(produit_pk))
    return _executer(
        request,
        tournee.date,
        lambda: services.declarer_retour(
            livraison,
            produit=produit,
            quantite=request.POST.get("quantite", "").strip().replace(",", "."),
            motif=request.POST.get("motif", ""),
            etat=request.POST.get("etat", ""),
        ),
        "Retour enregistré.",
    )


@login_required
@require_POST
def decider_retour(request, pk):
    _exiger(request, "ecrans.tournees")
    retour = get_object_or_404(
        Retour.objects.select_related("livraison__chargement__tournee"), pk=pk
    )
    return _executer(
        request,
        retour.livraison.chargement.tournee.date,
        lambda: services.decider_retour(retour, request.POST.get("decision", "")),
        "Décision enregistrée.",
    )


# --- Anomalies et saisies d'une tournée partie (§6, décision 8.4) -------------------------


def _livraison(pk):
    return get_object_or_404(
        Livraison.objects.select_related("chargement__tournee", "commande"), pk=pk
    )


@login_required
@require_POST
def reporter(request, pk):
    """Nouvelle date et consigne pour une livraison échouée : la commande est relancée."""
    _exiger(request, "ecrans.tournees")
    livraison = _livraison(pk)
    date = _date(request.POST.get("date")) if request.POST.get("date") else None
    return _executer(
        request,
        livraison.chargement.tournee.date,
        lambda: services.reporter_livraison(livraison, date, request.POST.get("consigne", "")),
        f"{livraison.commande} reportée : elle repasse « À planifier ».",
    )


@login_required
@require_POST
def demander_abandon(request, pk):
    """Abandon d'une commande en anomalie, décidé par un autre responsable (8.4)."""
    _exiger(request, "ecrans.tournees")
    livraison = _livraison(pk)
    try:
        commandes.demander_abandon(livraison.commande, request.POST.get("motif", ""))
    except commandes.ActionImpossible as erreur:
        messages.error(request, str(erreur))
    except ValidationError as erreur:
        messages.error(request, " ".join(erreur.messages))
    else:
        messages.success(request, "Abandon demandé : un autre responsable doit le valider.")
    return _retour(livraison.chargement.tournee.date)


@login_required
@require_POST
def decider_livraison(request, pk):
    """Accepter ou refuser une livraison saisie pour un prestataire sans compte."""
    _exiger(request, "ecrans.tournees")
    demande = get_object_or_404(
        Validation,
        pk=pk,
        content_type=ContentType.objects.get_for_model(Livraison),
        decision=Validation.Decision.EN_ATTENTE,
    )
    accepter = request.POST.get("decision") == "accepter"
    date = _livraison(demande.object_id).chargement.tournee.date
    try:
        services.decider_livraison_prestataire(demande, accepter=accepter)
    except (services.ActionImpossible, ValidationPropreDemande) as erreur:
        messages.error(request, str(erreur))
    else:
        messages.success(request, "Livraison validée." if accepter else "Saisie refusée.")
    return _retour(date)


# --- Reliquats (décisions 8.4 et du 3 novembre 2026) ------------------------------------


@login_required
@require_POST
def planifier_reliquat(request, pk):
    """Nouvelle date du reliquat : il entre au plan de production et devient affectable."""
    _exiger(request, "ecrans.tournees")
    commande = get_object_or_404(Commande, pk=pk)
    date = _date(request.POST.get("date")) if request.POST.get("date") else None
    return _executer(
        request,
        date or timezone.localdate(),
        lambda: services.planifier_reliquat(commande, date),
        f"Reliquat de {commande} planifié : il est à affecter à une tournée de ce jour.",
    )


@login_required
@require_POST
def demander_solde(request, pk):
    _exiger(request, "ecrans.tournees")
    commande = get_object_or_404(Commande, pk=pk)
    return _executer(
        request,
        _date(request.POST.get("retour")),
        lambda: services.demander_solde_reliquat(commande, request.POST.get("motif", "")),
        "Solde demandé : un autre responsable doit le valider.",
    )


@login_required
@require_POST
def decider_solde(request, pk):
    _exiger(request, "ecrans.tournees")
    demande = get_object_or_404(
        Validation,
        pk=pk,
        content_type=ContentType.objects.get_for_model(Livraison),
        decision=Validation.Decision.EN_ATTENTE,
    )
    accepter = request.POST.get("decision") == "accepter"
    try:
        services.decider_solde_reliquat(demande, accepter=accepter)
    except (services.ActionImpossible, ValidationPropreDemande) as erreur:
        messages.error(request, str(erreur))
    else:
        messages.success(
            request, "Reliquat soldé : la commande est livrée." if accepter else "Solde refusé."
        )
    return _retour(_date(request.POST.get("retour")))
