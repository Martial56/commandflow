"""Tournée en cours et preuve de livraison du livreur, sur téléphone (maquettes 5 et 6).

Une fois la tournée partie, « Ma tournée » montre la progression, le prochain arrêt
(« Je suis arrivé », itinéraire, preuve attendue), les arrêts à venir et ceux déjà
traités ; le livreur peut signaler un incident (décision 8.4). À l'arrêt, il saisit le
résultat, les quantités livrées et la preuve (maquette 6). Un livreur ne voit jamais la
tournée d'un autre (§22-25).
"""

import base64
import binascii
from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.core.files.base import ContentFile
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from catalogue.models import Unite
from entreprises.parametres import preuves_obligatoires
from noyau.models import Validation

from . import services
from .models import (
    LigneLivraison,
    Livraison,
    MotifEchec,
    MoyenAccord,
    StatutLivraison,
    TypeIncident,
)
from .services import STATUTS_LIVRAISON_TRAITEE, Resultat, libelle_exigences
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
    return redirect("livraison_saisie", livraison.pk)


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


# --- Preuve de livraison (maquette 6) ----------------------------------------------------

# Les quatre choix de la maquette 6 ; « Refusée » ouvre la liste des motifs du §12.3 (8.4).
RESULTATS = [
    ("livree", "Livrée"),
    ("partielle", "Partielle"),
    ("refusee", "Refusée"),
    ("client_absent", "Client absent"),
]
MOTIFS_REFUS = [(v, libelle) for v, libelle in MotifEchec.choices if v != MotifEchec.CLIENT_ABSENT]
TAILLE_MAX_PHOTO = 15 * 1024 * 1024
TAILLE_MAX_SIGNATURE = 512 * 1024
ENTETE_SIGNATURE = "data:image/png;base64,"
MESSAGES_RESULTAT = {
    StatutLivraison.LIVREE: "Livraison enregistrée.",
    StatutLivraison.PARTIELLEMENT_LIVREE: "Livraison partielle enregistrée : le reliquat est noté.",
    StatutLivraison.ECHOUEE: "Échec enregistré : le gestionnaire est prévenu.",
}


class SaisieInvalide(Exception):
    """Formulaire illisible (photo, signature, résultat) : message affiché tel quel."""


def _photo(fichier):
    if not fichier:
        return None
    if not (getattr(fichier, "content_type", "") or "").startswith("image/"):
        raise SaisieInvalide("La photo du bordereau doit être une image.")
    if fichier.size > TAILLE_MAX_PHOTO:
        raise SaisieInvalide("Photo trop lourde (15 Mo au plus).")
    return fichier


def _signature(donnee):
    """Signature dessinée à l'écran (signature_pad), reçue en PNG encodé."""
    if not donnee:
        return None
    illisible = SaisieInvalide("Signature illisible : la refaire.")
    if not donnee.startswith(ENTETE_SIGNATURE) or len(donnee) > TAILLE_MAX_SIGNATURE:
        raise illisible
    try:
        contenu = base64.b64decode(donnee[len(ENTETE_SIGNATURE) :], validate=True)
    except (binascii.Error, ValueError):
        raise illisible from None
    if not contenu.startswith(b"\x89PNG"):
        raise illisible
    return ContentFile(contenu, name="signature.png")


def _lire_saisie(request, livraison):
    """Formulaire de la maquette 6 → arguments de `services.cloturer_livraison`."""
    donnees = request.POST
    choix = donnees.get("resultat", "")
    if choix == "livree":
        resultat, motif = Resultat.LIVREE, ""
    elif choix == "partielle":
        resultat, motif = Resultat.PARTIELLE, donnees.get("motif_partielle", "")
    elif choix == "refusee":
        resultat, motif = Resultat.ECHEC, donnees.get("motif_refus", "")
    elif choix == "client_absent":
        resultat, motif = Resultat.ECHEC, MotifEchec.CLIENT_ABSENT
    else:
        raise SaisieInvalide("Choisir le résultat de la livraison.")
    arguments = {
        "motif": motif,
        "latitude": donnees.get("latitude"),
        "longitude": donnees.get("longitude"),
        "receptionnaire_nom": donnees.get("receptionnaire_nom", ""),
        "numero_bordereau": donnees.get("numero_bordereau", ""),
        "commentaire": donnees.get("commentaire", ""),
        "photo": _photo(request.FILES.get("photo")),
        "signature": _signature(donnees.get("signature", "")),
    }
    if resultat != Resultat.ECHEC:
        arguments["quantites"] = {
            ligne.pk: donnees.get(f"livre-{ligne.pk}", "").strip().replace(",", ".")
            for ligne in livraison.lignes.all()
        }
    if resultat == Resultat.PARTIELLE:
        arguments["accord"] = {
            "nom_client": donnees.get("accord_nom", ""),
            "moyen": donnees.get("accord_moyen", ""),
        }
    return resultat, arguments


@login_required
def saisir(request, pk):
    """Résultat, quantités livrées et preuve d'un arrêt (maquette 6)."""
    livraison = get_object_or_404(
        Livraison.objects.select_related(
            "chargement__tournee", "commande__client", "commande__adresse"
        ),
        pk=pk,
    )
    tournee = _tournee(request, livraison.chargement.tournee_id)
    if livraison.statut not in (StatutLivraison.EN_ATTENTE, StatutLivraison.SUR_PLACE):
        messages.info(request, f"Arrêt déjà traité ({livraison.get_statut_display()}).")
        return redirect("reception_tournee", tournee.pk)
    erreur = ""
    if request.method == "POST":
        try:
            resultat, arguments = _lire_saisie(request, livraison)
            livraison = services.cloturer_livraison(livraison, resultat, **arguments)
        except (SaisieInvalide, services.ActionImpossible) as exception:
            erreur = str(exception)
        else:
            messages.success(
                request,
                MESSAGES_RESULTAT.get(
                    livraison.statut, "Saisie enregistrée : un autre responsable doit la valider."
                ),
            )
            return redirect("reception_tournee", tournee.pk)

    lignes = livraison.lignes.select_related("ligne_commande__produit").order_by("pk")
    return render(
        request,
        "logistique/livraison.html",
        {
            "livraison": livraison,
            "tournee": tournee,
            "lignes": [
                (
                    ligne,
                    request.POST.get(f"livre-{ligne.pk}", f"{ligne.quantite_prevue.normalize():f}"),
                )
                for ligne in lignes
            ],
            "arrets": Livraison.objects.filter(chargement__tournee=tournee).count(),
            "itineraire": lien_itineraire(livraison.commande.adresse),
            "preuve": preuve_attendue(livraison.commande),
            "resultats": RESULTATS,
            "motifs_refus": MOTIFS_REFUS,
            "moyens_accord": MoyenAccord.choices,
            "valeurs": request.POST,
            "erreur": erreur,
            "pour_le_prestataire": tournee.compte_livreur is None,
        },
        status=400 if erreur else 200,
    )
