import time

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from catalogue.models import Produit
from clients.models import Client
from noyau.contexte import entreprise_obligatoire
from noyau.fichiers import url_signee
from noyau.models import PieceJointe, Validation

from . import services
from .forms import (
    DemandeModificationForm,
    EnTeteCommandeForm,
    LigneSaisie,
    MotifForm,
    NoteConfirmationForm,
    libelle_client,
    lire_lignes,
    valider_lignes,
)
from .historique import historique, parcours
from .models import (
    STATUTS_ANNULATION_SIMPLE,
    STATUTS_MODIFICATION_LIBRE,
    STATUTS_MODIFICATION_VALIDEE,
    Canal,
    Commande,
    Statut,
)

# Onglets de la liste (maquette 9) : libellé et statuts regroupés.
ONGLETS = [
    ("toutes", "Toutes", None),
    ("a_confirmer", "À confirmer", [Statut.A_CONFIRMER]),
    ("en_preparation", "En préparation", [Statut.EN_PREPARATION]),
    ("en_livraison", "En livraison", [Statut.EN_LIVRAISON]),
    ("livrees", "Livrées", [Statut.LIVREE, Statut.PARTIELLEMENT_LIVREE]),
    ("en_anomalie", "En anomalie", [Statut.EN_ANOMALIE]),
]
PAR_PAGE = 25


def _exiger(request, permission, objet=None):
    if not request.user.has_perm(permission, objet):
        raise PermissionDenied


def _produits(inclure=()):
    return Produit.objects.filter(Q(actif=True) | Q(pk__in=inclure)).order_by("nom")


def _total(lignes):
    return sum((ligne.montant or 0) for ligne in lignes)


# --- Liste (maquette 9) ----------------------------------------------------------


@login_required
def liste(request):
    _exiger(request, "commandes.voir_commande")
    commandes = Commande.objects.select_related("client").annotate(
        total=Coalesce(
            Sum("lignes__montant", filter=Q(lignes__retiree=False)),
            Value(0),
            output_field=DecimalField(max_digits=14, decimal_places=0),
        )
    )
    recherche = request.GET.get("q", "").strip()
    if recherche:
        commandes = commandes.filter(
            Q(numero__icontains=recherche)
            | Q(client__nom__icontains=recherche)
            | Q(client__telephone__icontains=recherche)
        )
    if request.GET.get("date"):
        commandes = commandes.filter(date_livraison=request.GET["date"])
    canal = request.GET.get("canal", "")
    if canal == "recurrente":
        commandes = commandes.filter(modele_recurrent__isnull=False)
    elif canal in Canal.values:
        commandes = commandes.filter(canal=canal)
    if request.GET.get("urgentes"):
        commandes = commandes.filter(urgente=True)

    comptes = commandes.aggregate(
        **{
            code: Count("pk", filter=Q(statut__in=statuts) if statuts else Q())
            for code, _, statuts in ONGLETS
        }
    )
    onglet = request.GET.get("statut", "toutes")
    statuts = dict((code, statuts) for code, _, statuts in ONGLETS).get(onglet)
    if statuts:
        commandes = commandes.filter(statut__in=statuts)

    page = Paginator(commandes.order_by("-date_livraison", "-numero"), PAR_PAGE).get_page(
        request.GET.get("page")
    )
    contexte = {
        "page": page,
        "onglets": [(code, libelle, comptes[code]) for code, libelle, _ in ONGLETS],
        "onglet": onglet,
        "filtres": request.GET,
        "canaux": [("recurrente", "Récurrente"), *Canal.choices],
        "peut_creer": request.user.has_perm("commandes.creer_commande"),
    }
    if request.headers.get("HX-Request"):
        return render(request, "commandes/_liste.html", contexte)
    return render(request, "commandes/liste.html", contexte)


# --- Fiche (maquette 10) -----------------------------------------------------------


@login_required
def detail(request, pk):
    _exiger(request, "commandes.voir_commande")
    commande = get_object_or_404(Commande.objects.select_related("client", "adresse"), pk=pk)
    utilisateur = request.user
    demandes = Validation.objects.filter(
        content_type__app_label="commandes",
        content_type__model="commande",
        object_id=commande.pk,
        decision=Validation.Decision.EN_ATTENTE,
    ).select_related("demandee_par")
    pieces = PieceJointe.objects.filter(
        content_type__app_label="commandes", content_type__model="commande", object_id=commande.pk
    )
    peut_saisir = utilisateur.has_perm("commandes.creer_commande")
    statut = commande.statut
    return render(
        request,
        "commandes/detail.html",
        {
            "commande": commande,
            "lignes": commande.lignes_actives.select_related("produit"),
            "etapes": parcours(commande),
            "historique": historique(commande),
            "demandes": demandes,
            "pieces": [(piece, url_signee(piece, utilisateur)) for piece in pieces],
            "peut_decider": utilisateur.has_perm("commandes.valider_commande", commande),
            "peut_modifier": peut_saisir and statut in STATUTS_MODIFICATION_LIBRE,
            "peut_soumettre": statut == Statut.BROUILLON
            and utilisateur.has_perm("commandes.soumettre_commande", commande),
            "peut_confirmer": statut == Statut.A_CONFIRMER
            and utilisateur.has_perm("commandes.confirmer_commande", commande),
            "peut_annuler": statut in STATUTS_ANNULATION_SIMPLE
            and utilisateur.has_perm("commandes.annuler_commande", commande),
            "peut_demander": peut_saisir and statut in STATUTS_MODIFICATION_VALIDEE,
            "formulaire_note": NoteConfirmationForm(),
            "formulaire_motif": MotifForm(),
        },
    )


def _action(request, pk, fonction, succes):
    """Exécute un service sur la commande et revient à sa fiche avec un message."""
    commande = get_object_or_404(Commande, pk=pk)
    try:
        fonction(commande)
    except (services.ActionImpossible, ValidationError) as erreur:
        texte = " ".join(erreur.messages) if isinstance(erreur, ValidationError) else str(erreur)
        messages.error(request, texte)
    else:
        messages.success(request, succes)
    return redirect("commande_detail", pk=pk)


@login_required
@require_POST
def soumettre(request, pk):
    return _action(request, pk, services.soumettre_commande, "Commande soumise pour confirmation.")


@login_required
@require_POST
def confirmer(request, pk):
    note = request.POST.get("note", "").strip()
    return _action(
        request,
        pk,
        lambda commande: services.confirmer_commande(commande, note=note),
        "Commande confirmée avec le client.",
    )


@login_required
@require_POST
def annuler(request, pk):
    motif = request.POST.get("motif", "")
    return _action(
        request,
        pk,
        lambda commande: services.annuler_commande(commande, motif),
        "Commande annulée.",
    )


@login_required
@require_POST
def demander_annulation(request, pk):
    motif = request.POST.get("motif", "")
    return _action(
        request,
        pk,
        lambda commande: services.demander_annulation(commande, motif),
        "Demande d'annulation envoyée à un responsable.",
    )


@login_required
@require_POST
def decider(request, pk, validation_pk):
    validation = get_object_or_404(
        Validation,
        pk=validation_pk,
        object_id=pk,
        content_type__app_label="commandes",
        content_type__model="commande",
    )
    accepter = request.POST.get("decision") == "accepter"
    return _action(
        request,
        pk,
        lambda commande: services.decider_validation_commande(validation, accepter=accepter),
        "Demande acceptée et appliquée." if accepter else "Demande refusée.",
    )


# --- Saisie : création, modification, demande de modification -----------------------


def _editeur(request, formulaire, lignes, **contexte):
    return render(
        request,
        "commandes/edition.html",
        {
            "formulaire": formulaire,
            "lignes": lignes,
            "total": _total(lignes),
            "produits": _produits(inclure=contexte.pop("inclure", ())),
            **contexte,
        },
    )


def _lignes_de(commande):
    return [
        LigneSaisie(
            ligne.pk,
            ligne.produit,
            f"{ligne.quantite.normalize():f}".replace(".", ","),
            prix_fige=ligne.prix_unitaire,
        )  # fmt: skip
        for ligne in commande.lignes_actives.select_related("produit")
    ]


def _prix_figes(commande):
    return {ligne.produit_id: ligne.prix_unitaire for ligne in commande.lignes_actives}


def _erreurs_service(formulaire, erreur):
    for champ, textes in erreur.message_dict.items():
        formulaire.add_error(champ if champ in formulaire.fields else None, textes)


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
    return _editeur(
        request,
        formulaire,
        lignes,
        mode="creation",
        clients=Client.objects.filter(actif=True).only("nom", "code"),
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
        _erreurs_service(formulaire, erreur)
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


def _initial(commande):
    return {
        "canal": commande.canal,
        "adresse": commande.adresse,
        "date_livraison": commande.date_livraison,
        "creneau": commande.creneau,
        "urgente": commande.urgente,
        "instructions": commande.instructions,
    }


@login_required
def modifier(request, pk):
    """Modification libre d'une commande avant confirmation (même écran que la saisie)."""
    commande = get_object_or_404(Commande, pk=pk)
    _exiger(request, "commandes.soumettre_commande", commande)
    if commande.statut not in STATUTS_MODIFICATION_LIBRE:
        messages.error(request, "Commande déjà confirmée : demander une modification.")
        return redirect("commande_detail", pk=pk)
    entreprise = entreprise_obligatoire()
    prix_figes = _prix_figes(commande)
    if request.method == "POST":
        formulaire = _formulaire_modification(
            commande, request.POST, request.FILES, entreprise=entreprise
        )
        lignes = lire_lignes(request.POST, prix_figes)
        valides = valider_lignes(lignes)
        if formulaire.is_valid() and not any(ligne.erreur for ligne in lignes):
            donnees = dict(formulaire.cleaned_data)
            justificatif = donnees.pop("justificatif")
            try:
                with transaction.atomic():
                    services.modifier_commande(commande, lignes=valides, **donnees)
                    if justificatif is not None:
                        services.joindre_justificatif(commande, justificatif)
            except ValidationError as erreur:
                _erreurs_service(formulaire, erreur)
            else:
                messages.success(request, f"Commande {commande.numero} modifiée.")
                return redirect("commande_detail", pk=pk)
    else:
        formulaire = _formulaire_modification(
            commande, initial=_initial(commande), entreprise=entreprise
        )
        lignes = _lignes_de(commande)
    return _editeur(
        request, formulaire, lignes, mode="modification", commande=commande, inclure=prix_figes
    )


def _formulaire_modification(commande, *args, **kwargs):
    formulaire = EnTeteCommandeForm(*args, client_fixe=commande.client, **kwargs)
    if commande.modele_recurrent_id:
        del formulaire.fields["canal"]  # commande générée : pas de canal de réception
    return formulaire


@login_required
def demander_modification(request, pk):
    """Demande de modification d'une commande confirmée, soumise à un responsable."""
    commande = get_object_or_404(Commande, pk=pk)
    _exiger(request, "commandes.creer_commande")
    if commande.statut not in STATUTS_MODIFICATION_VALIDEE:
        messages.error(request, "Cette commande ne peut pas faire l'objet d'une demande.")
        return redirect("commande_detail", pk=pk)
    entreprise = entreprise_obligatoire()
    prix_figes = _prix_figes(commande)
    if request.method == "POST":
        formulaire = DemandeModificationForm(
            request.POST, entreprise=entreprise, client_fixe=commande.client
        )
        lignes = lire_lignes(request.POST, prix_figes)
        valides = valider_lignes(lignes)
        if formulaire.is_valid() and not any(ligne.erreur for ligne in lignes):
            donnees = dict(formulaire.cleaned_data)
            motif = donnees.pop("motif")
            changements = {
                champ: valeur
                for champ, valeur in donnees.items()
                if valeur != getattr(commande, champ)
            }
            actuelles = {(p.pk, q) for p, q in _lignes_actuelles(commande)}
            nouvelles = None if {(p.pk, q) for p, q in valides} == actuelles else valides
            try:
                services.demander_modification(commande, motif, lignes=nouvelles, **changements)
            except (ValidationError, services.ActionImpossible) as erreur:
                textes = erreur.messages if isinstance(erreur, ValidationError) else [str(erreur)]
                formulaire.add_error(None, " ".join(textes))
            else:
                messages.success(request, "Demande de modification envoyée à un responsable.")
                return redirect("commande_detail", pk=pk)
    else:
        formulaire = DemandeModificationForm(
            initial=_initial(commande), entreprise=entreprise, client_fixe=commande.client
        )
        lignes = _lignes_de(commande)
    return _editeur(
        request, formulaire, lignes, mode="demande", commande=commande, inclure=prix_figes
    )


def _lignes_actuelles(commande):
    return [
        (ligne.produit, ligne.quantite)
        for ligne in commande.lignes_actives.select_related("produit")
    ]


# --- Fragments htmx ------------------------------------------------------------------


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
    """Unités, prix, montants et total recalculés côté serveur (htmx, échanges hors cible).

    Pour une commande existante, les lignes déjà présentes gardent leur prix figé.
    """
    _exiger(request, "commandes.creer_commande")
    prix_figes = {}
    if request.POST.get("commande", "").isdigit():
        commande = get_object_or_404(Commande, pk=request.POST["commande"])
        prix_figes = _prix_figes(commande)
    lignes = lire_lignes(request.POST, prix_figes)
    return render(request, "commandes/_calcul.html", {"lignes": lignes, "total": _total(lignes)})
