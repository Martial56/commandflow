"""Tournée en cours du livreur, sur téléphone (maquette 5).

Une fois la tournée partie, « Ma tournée » montre la progression, le prochain arrêt
(« Je suis arrivé », itinéraire, preuve attendue), les arrêts à venir et ceux déjà
traités ; le livreur peut signaler un incident (décision 8.4). Un livreur ne voit jamais
la tournée d'un autre (§22-25).
"""

from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from catalogue.models import Unite
from entreprises.parametres import preuves_obligatoires
from noyau.models import Validation

from . import services
from .models import LigneLivraison, Livraison, StatutLivraison, TypeIncident
from .services import STATUTS_LIVRAISON_TRAITEE, libelle_exigences
from .views_reception import _tournee

PASTILLES = {
    StatutLivraison.LIVREE: ("Livrée", "succes"),
    StatutLivraison.PARTIELLEMENT_LIVREE: ("Partielle", "succes"),
    StatutLivraison.ECHOUEE: ("Échec", "alerte"),
    StatutLivraison.REPORTEE: ("Reportée", "alerte"),
}


def lien_itineraire(adresse):
    """Itinéraire dans l'application de navigation du téléphone (§13) : vers les
    coordonnées GPS de l'adresse si elles sont connues, sinon vers l'adresse écrite."""
    if adresse.latitude is not None and adresse.longitude is not None:
        destination = f"{adresse.latitude},{adresse.longitude}"
    else:
        destination = ", ".join(
            partie for partie in (adresse.adresse, adresse.commune, "Côte d'Ivoire") if partie
        )
    return f"https://www.google.com/maps/dir/?api=1&destination={quote(destination)}"


def preuve_attendue(commande):
    """« photo du bordereau ou nom du réceptionnaire » (décision 8.4)."""
    return libelle_exigences(preuves_obligatoires(commande.entreprise, commande.client.categorie))


def _resume(livraison):
    """« 3 produits · 175 pièces » (pièces seulement si tout est vendu à la pièce)."""
    lignes = list(livraison.lignes.all())
    texte = f"{len(lignes)} produit{'s' if len(lignes) > 1 else ''}"
    if lignes and all(ligne.ligne_commande.produit.unite == Unite.PIECE for ligne in lignes):
        total = sum(ligne.quantite_prevue for ligne in lignes)
        texte += f" · {total.normalize():f} pièce{'s' if total > 1 else ''}"
    return texte


def _contexte(tournee):
    lignes = LigneLivraison.objects.select_related("ligne_commande__produit")
    livraisons = list(
        Livraison.objects.filter(chargement__tournee=tournee)
        .select_related("chargement", "commande__client", "commande__adresse")
        .prefetch_related(Prefetch("lignes", queryset=lignes))
        .order_by("chargement__ordre_passage", "pk")
    )
    # Saisies du gestionnaire pour un prestataire sans compte, en attente de décision.
    en_validation = set(
        Validation.objects.filter(
            content_type=ContentType.objects.get_for_model(Livraison),
            object_id__in=[livraison.pk for livraison in livraisons],
            action=Validation.Action.EXCEPTION,
            decision=Validation.Decision.EN_ATTENTE,
        ).values_list("object_id", flat=True)
    )
    a_faire, traitees = [], []
    for livraison in livraisons:
        livraison.en_validation = livraison.pk in en_validation
        if livraison.statut in STATUTS_LIVRAISON_TRAITEE:
            livraison.pastille = PASTILLES[livraison.statut]
            traitees.append(livraison)
        else:
            a_faire.append(livraison)
    # Le prochain arrêt : celui où le livreur est déjà, sinon le premier à faire.
    prochain = next((liv for liv in a_faire if liv.statut == StatutLivraison.SUR_PLACE), None)
    prochain = prochain or next((liv for liv in a_faire if not liv.en_validation), None)
    if prochain is not None:
        prochain.resume = _resume(prochain)
        prochain.preuve = preuve_attendue(prochain.commande)
        prochain.itineraire = lien_itineraire(prochain.commande.adresse)
    total = len(livraisons)
    return {
        "tournee": tournee,
        "prochain": prochain,
        "a_venir": [livraison for livraison in a_faire if livraison is not prochain],
        "traitees": list(reversed(traitees)),
        "nombre_traitees": len(traitees),
        "total": total,
        "progression": round(100 * len(traitees) / total) if total else 0,
        "types_incident": TypeIncident.choices,
        "pour_le_prestataire": tournee.compte_livreur is None,
    }


def afficher(request, tournee):
    contexte = _contexte(tournee)
    if request.headers.get("HX-Request"):
        return render(request, "logistique/_arrets.html", {**contexte, "fragment": True})
    return render(request, "logistique/tournee_en_cours.html", contexte)


@login_required
@require_POST
def arriver(request, pk):
    """« Je suis arrivé » : heure et position (si le téléphone la donne)."""
    livraison = get_object_or_404(Livraison.objects.select_related("chargement"), pk=pk)
    tournee = _tournee(request, livraison.chargement.tournee_id)
    try:
        services.arriver_chez_client(
            livraison, request.POST.get("latitude"), request.POST.get("longitude")
        )
    except services.ActionImpossible as erreur:
        messages.error(request, str(erreur))
    return redirect("reception_tournee", tournee.pk)


@login_required
@require_POST
def incident(request, pk):
    """Signaler un incident (panne, accident…) au gestionnaire."""
    tournee = _tournee(request, pk)
    try:
        services.signaler_incident(
            tournee, request.POST.get("type", ""), request.POST.get("commentaire", "")
        )
    except services.ActionImpossible as erreur:
        messages.error(request, str(erreur))
    else:
        messages.success(request, "Incident signalé au gestionnaire.")
    return redirect("reception_tournee", tournee.pk)
