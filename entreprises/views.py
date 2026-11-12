"""Écran Paramètres (maquette 17, décision 8.6) : comptes, preuves exigées, règles."""

from allauth.mfa.models import Authenticator
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from clients.models import Categorie
from comptes import services as comptes
from comptes.models import Role, Utilisateur
from comptes.rules import est_direction

from . import parametres
from . import services as reglages
from .forms import FormulaireNouveauCompte, FormulaireRegles

MODES = [("un", "Au moins un"), ("tous", "Tous")]


def _exiger(request, permission="ecrans.parametres", objet=None):
    if not request.user.has_perm(permission, objet):
        raise PermissionDenied


def _derniere_connexion(compte):
    """« aujourd'hui 08h45 », « hier 17h20 », ou la date."""
    if compte.last_login is None:
        return "jamais", None
    moment = timezone.localtime(compte.last_login)
    ecart = (timezone.localdate() - moment.date()).days
    jour = {0: "aujourd'hui", 1: "hier"}.get(ecart)
    return jour, moment


def _comptes(request):
    entreprise = request.user.entreprise
    avec_code = set(
        Authenticator.objects.filter(
            user__entreprise=entreprise, type=Authenticator.Type.TOTP
        ).values_list("user_id", flat=True)
    )
    lignes = []
    for compte in Utilisateur.objects.filter(entreprise=entreprise).order_by(
        "-is_active", "first_name", "last_name"
    ):
        jour, moment = _derniere_connexion(compte)
        lignes.append(
            {
                "compte": compte,
                "role": Role(compte.role_principal).label if compte.role_principal else "—",
                "jour": jour,
                "moment": moment,
                "code": compte.pk in avec_code,
                "gerable": request.user.has_perm("comptes.gerer_compte", compte),
            }
        )
    return lignes


def _preuves(entreprise):
    grille = []
    for categorie in Categorie:
        moyens, mode = parametres.reglage_de_preuve(entreprise, categorie.value)
        grille.append(
            {
                "categorie": categorie,
                "cases": [
                    (moyen, libelle, moyen in moyens)
                    for moyen, libelle in parametres.MOYENS_DE_PREUVE
                ],
                "mode": mode,
            }
        )
    return grille


def _formulaire_regles(entreprise):
    return FormulaireRegles(
        initial={
            "generation": parametres.heure_generation_recurrentes(entreprise),
            "limite": parametres.heure_limite_confirmation(entreprise),
            "resume": parametres.heure_resume(entreprise),
            "creneaux": "\n".join(parametres.creneaux(entreprise)),
        }
    )


def _page(request, *, nouveau=None, regles=None, provisoire=None, status=200):
    entreprise = request.user.entreprise
    direction = est_direction(request.user)
    return render(
        request,
        "entreprises/parametres.html",
        {
            "comptes": _comptes(request),
            "peut_gerer_comptes": request.user.has_perm("comptes.gerer_comptes"),
            "peut_modifier": request.user.has_perm("entreprises.modifier_parametres"),
            "nouveau": nouveau or FormulaireNouveauCompte(direction=direction),
            "nouveau_ouvert": nouveau is not None and bool(nouveau.errors),
            "regles": regles or _formulaire_regles(entreprise),
            "preuves": _preuves(entreprise),
            "moyens": parametres.MOYENS_DE_PREUVE,
            "modes": MODES,
            "provisoire": provisoire,
        },
        status=status,
    )


@login_required
def ecran(request):
    _exiger(request)
    return _page(request)


# --- Comptes ------------------------------------------------------------------------


@login_required
@require_POST
def ajouter_compte(request):
    _exiger(request)
    _exiger(request, "comptes.gerer_comptes")
    formulaire = FormulaireNouveauCompte(request.POST, direction=est_direction(request.user))
    if not formulaire.is_valid():
        return _page(request, nouveau=formulaire, status=400)
    donnees = formulaire.cleaned_data
    try:
        compte, provisoire = comptes.creer_compte(
            prenom=donnees["prenom"],
            nom=donnees["nom"],
            telephone=donnees["telephone"],
            role=donnees["role"],
            email=donnees["email"],
        )
    except comptes.ActionImpossible as erreur:
        formulaire.add_error(None, str(erreur))
        return _page(request, nouveau=formulaire, status=400)
    messages.success(request, f"Compte de {compte} créé.")
    return _page(request, provisoire=(compte, provisoire))


def _compte(request, pk):
    return get_object_or_404(Utilisateur, pk=pk, entreprise=request.user.entreprise)


@login_required
@require_POST
def reinitialiser(request, pk):
    _exiger(request)
    compte = _compte(request, pk)
    _exiger(request, "comptes.gerer_compte", compte)
    provisoire = comptes.reinitialiser_mot_de_passe(compte)
    messages.success(request, f"Mot de passe de {compte} réinitialisé.")
    return _page(request, provisoire=(compte, provisoire))


def _basculer(request, pk, action, succes):
    _exiger(request)
    compte = _compte(request, pk)
    _exiger(request, "comptes.gerer_compte", compte)
    try:
        action(compte)
    except comptes.ActionImpossible as erreur:
        messages.error(request, str(erreur))
    else:
        messages.success(request, succes.format(compte=compte))
    return redirect("ecran_parametres")


@login_required
@require_POST
def desactiver(request, pk):
    return _basculer(
        request,
        pk,
        comptes.desactiver_compte,
        "Compte de {compte} désactivé : il ne peut plus se connecter.",
    )


@login_required
@require_POST
def reactiver(request, pk):
    return _basculer(request, pk, comptes.reactiver_compte, "Compte de {compte} réactivé.")


# --- Réglages -----------------------------------------------------------------------


@login_required
@require_POST
def regles(request):
    _exiger(request)
    _exiger(request, "entreprises.modifier_parametres")
    formulaire = FormulaireRegles(request.POST)
    if not formulaire.is_valid():
        return _page(request, regles=formulaire, status=400)
    donnees = formulaire.cleaned_data
    try:
        reglages.enregistrer_regles(
            generation=donnees["generation"],
            limite=donnees["limite"],
            resume=donnees["resume"],
            creneaux=formulaire.liste_creneaux(),
        )
    except reglages.ActionImpossible as erreur:
        formulaire.add_error(None, str(erreur))
        return _page(request, regles=formulaire, status=400)
    messages.success(request, "Règles de fonctionnement enregistrées.")
    return redirect("ecran_parametres")


@login_required
@require_POST
def preuves(request):
    _exiger(request)
    _exiger(request, "entreprises.modifier_parametres")
    choix = {
        categorie: (
            [
                moyen
                for moyen, _ in parametres.MOYENS_DE_PREUVE
                if request.POST.get(f"preuve-{categorie.value}-{moyen}")
            ],
            request.POST.get(f"mode-{categorie.value}", ""),
        )
        for categorie in Categorie
    }
    try:
        reglages.enregistrer_preuves(choix)
    except reglages.ActionImpossible as erreur:
        messages.error(request, str(erreur))
    else:
        messages.success(request, "Preuves de livraison enregistrées.")
    return redirect("ecran_parametres")
