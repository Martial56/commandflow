import time

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from catalogue.models import Produit
from clients.models import Client
from noyau.contexte import entreprise_obligatoire
from noyau.fichiers import url_signee
from noyau.models import PieceJointe

from . import services
from .forms import (
    EnTeteCommandeForm,
    LigneSaisie,
    libelle_client,
    lire_lignes,
    valider_lignes,
)
from .models import Commande


def _exiger(request, permission, objet=None):
    if not request.user.has_perm(permission, objet):
        raise PermissionDenied


def _produits():
    return Produit.objects.filter(actif=True).order_by("nom")


def _total(lignes):
    return sum((ligne.montant or 0) for ligne in lignes)


@login_required
def nouvelle(request):
    """Écran « Nouvelle commande » (maquette 1)."""
    _exiger(request, "commandes.creer_commande")
    entreprise = entreprise_obligatoire()
    if request.method == "POST":
        formulaire = EnTeteCommandeForm(request.POST, request.FILES, entreprise=entreprise)
        lignes = lire_lignes(request.POST)
        valides = valider_lignes(lignes)
        if formulaire.is_valid() and not any(ligne.erreur for ligne in lignes):
            commande = _enregistrer(request, formulaire, valides)
            if commande is not None:
                return redirect("commande_detail", pk=commande.pk)
    else:
        client = Client.objects.filter(pk=request.GET.get("client"), actif=True).first()
        formulaire = EnTeteCommandeForm(
            initial={"client": libelle_client(client)} if client else {}, entreprise=entreprise
        )
        lignes = [LigneSaisie(int(time.time() * 1000))]
    return render(
        request,
        "commandes/nouvelle.html",
        {
            "formulaire": formulaire,
            "lignes": lignes,
            "total": _total(lignes),
            "produits": _produits(),
            "clients": Client.objects.filter(actif=True).only("nom", "code"),
        },
    )


def _enregistrer(request, formulaire, lignes):
    """Crée la commande (et la soumet si demandé). Renvoie None si le service refuse."""
    donnees = formulaire.cleaned_data
    try:
        with transaction.atomic():
            commande = services.creer_commande(
                client=donnees["client"],
                adresse=donnees["adresse"],
                canal=donnees["canal"],
                date_livraison=donnees["date_livraison"],
                creneau=donnees["creneau"],
                urgente=donnees["urgente"],
                instructions=donnees["instructions"],
                lignes=lignes,
                justificatif=donnees["justificatif"],
            )
    except ValidationError as erreur:
        for champ, textes in erreur.message_dict.items():
            formulaire.add_error(champ if champ in formulaire.fields else None, textes)
        return None

    if request.POST.get("action") == "soumettre":
        try:
            services.soumettre_commande(commande)
        except services.ActionImpossible as erreur:
            messages.warning(request, f"Brouillon {commande.numero} enregistré. {erreur}")
            return commande
        suite = " Demande de validation de l'urgence envoyée." if commande.urgente else ""
        messages.success(request, f"Commande {commande.numero} soumise pour confirmation.{suite}")
    else:
        messages.success(request, f"Brouillon {commande.numero} enregistré.")
    return commande


@login_required
@require_GET
def adresses(request):
    """Liste des adresses du client saisi (rechargée par htmx)."""
    _exiger(request, "commandes.creer_commande")
    formulaire = EnTeteCommandeForm(request.GET, entreprise=entreprise_obligatoire())
    return render(request, "commandes/_adresse.html", {"champ": formulaire["adresse"]})


@login_required
@require_GET
def ligne(request):
    """Nouvelle ligne vide du tableau des produits (htmx)."""
    _exiger(request, "commandes.creer_commande")
    index = request.GET.get("index", "")
    if not index.isdigit():
        index = str(int(time.time() * 1000))
    return render(
        request, "commandes/_ligne.html", {"ligne": LigneSaisie(index), "produits": _produits()}
    )


@login_required
@require_POST
def calcul(request):
    """Unités, prix, montants et total recalculés côté serveur (htmx, échanges hors cible)."""
    _exiger(request, "commandes.creer_commande")
    lignes = lire_lignes(request.POST)
    return render(request, "commandes/_calcul.html", {"lignes": lignes, "total": _total(lignes)})


@login_required
def detail(request, pk):
    """Fiche commande (complétée à l'étape suivante selon la maquette 10)."""
    _exiger(request, "commandes.voir_commande")
    commande = get_object_or_404(Commande, pk=pk)
    pieces = PieceJointe.objects.filter(
        content_type__model="commande", content_type__app_label="commandes", object_id=commande.pk
    )
    return render(
        request,
        "commandes/detail.html",
        {
            "commande": commande,
            "lignes": commande.lignes_actives.select_related("produit"),
            "pieces": [(piece, url_signee(piece, request.user)) for piece in pieces],
        },
    )
