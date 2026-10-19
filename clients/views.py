from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import services
from .forms import (
    AdresseForm,
    ClientForm,
    ConfirmationDoublonForm,
    ContactFacultatifForm,
    ContactForm,
)
from .models import Adresse, Client


def _exiger(request, permission, objet=None):
    if not request.user.has_perm(permission, objet):
        raise PermissionDenied


def _clients_filtres(request):
    recherche = request.GET.get("q", "").strip()
    inactifs = request.GET.get("inactifs") == "1"
    clients = Client.objects.all() if inactifs else Client.objects.filter(actif=True)
    if recherche:
        clients = clients.filter(
            Q(nom__icontains=recherche)
            | Q(code__icontains=recherche)
            | Q(telephone__icontains=recherche)
        )
    return clients, recherche, inactifs


def _rendre(request, client=None, **contexte):
    clients, recherche, inactifs = _clients_filtres(request)
    contexte = {
        "clients": clients[:200],
        "recherche": recherche,
        "inactifs": inactifs,
        "client": client,
        "peut_creer": request.user.has_perm("clients.creer_client"),
        "peut_gerer": request.user.has_perm("clients.gerer_client"),
        **contexte,
    }
    if request.headers.get("HX-Request") and request.headers.get("HX-Target") == "liste-clients":
        return render(request, "clients/_liste.html", contexte)
    return render(request, "clients/fiche.html", contexte)


@login_required
def liste(request):
    _exiger(request, "clients.voir_client")
    return _rendre(request)


@login_required
def detail(request, pk):
    _exiger(request, "clients.voir_client")
    client = get_object_or_404(Client, pk=pk)
    return _rendre(
        request,
        client=client,
        adresses=client.adresses.filter(actif=True),
        contacts=client.contacts.all(),
    )


@login_required
def nouveau(request):
    _exiger(request, "clients.creer_client")
    formulaire = ClientForm(request.POST or None)
    adresse = AdresseForm(request.POST or None, prefix="adresse")
    contact = ContactFacultatifForm(request.POST or None, prefix="contact")
    confirmation = ConfirmationDoublonForm(request.POST or None)
    doublons = []
    if request.method == "POST" and all(
        f.is_valid() for f in (formulaire, adresse, contact, confirmation)
    ):
        try:
            client = services.creer_client(
                adresse=adresse.cleaned_data,
                contact=contact.cleaned_data if contact.est_rempli() else None,
                forcer_doublon=confirmation.cleaned_data["forcer_doublon"],
                **formulaire.cleaned_data,
            )
        except services.DoublonPotentiel as doublon:
            doublons = doublon.clients
        else:
            messages.success(request, f"Client {client.code} créé.")
            return redirect("client_detail", pk=client.pk)
    return render(
        request,
        "clients/nouveau.html",
        {
            "formulaire": formulaire,
            "adresse": adresse,
            "contact": contact,
            "confirmation": confirmation,
            "doublons": doublons,
        },
    )


@login_required
def modifier(request, pk):
    client = get_object_or_404(Client, pk=pk)
    _exiger(request, "clients.gerer_ce_client", client)
    formulaire = ClientForm(request.POST or None, instance=client)
    if request.method == "POST" and formulaire.is_valid():
        services.modifier_client(client, **formulaire.cleaned_data)
        messages.success(request, "Fiche client enregistrée.")
        return redirect("client_detail", pk=client.pk)
    return render(
        request,
        "clients/formulaire.html",
        {"client": client, "formulaire": formulaire, "titre": f"Modifier {client.nom}"},
    )


@login_required
@require_POST
def changer_activite(request, pk, actif):
    client = get_object_or_404(Client, pk=pk)
    _exiger(request, "clients.gerer_ce_client", client)
    if actif:
        services.reactiver_client(client)
        messages.success(request, "Client réactivé.")
    else:
        services.desactiver_client(client)
        messages.success(request, "Client désactivé : il n'est plus proposé dans les commandes.")
    return redirect("client_detail", pk=client.pk)


@login_required
def ajouter_adresse(request, pk):
    client = get_object_or_404(Client, pk=pk)
    _exiger(request, "clients.gerer_ce_client", client)
    formulaire = AdresseForm(request.POST or None)
    if request.method == "POST" and formulaire.is_valid():
        services.ajouter_adresse(client, **formulaire.cleaned_data)
        messages.success(request, "Adresse ajoutée.")
        return redirect("client_detail", pk=client.pk)
    return render(
        request,
        "clients/formulaire.html",
        {"client": client, "formulaire": formulaire, "titre": "Nouvelle adresse de livraison"},
    )


@login_required
@require_POST
def action_adresse(request, pk, adresse_pk, action):
    client = get_object_or_404(Client, pk=pk)
    _exiger(request, "clients.gerer_ce_client", client)
    adresse = get_object_or_404(Adresse, pk=adresse_pk, client=client)
    try:
        if action == "par_defaut":
            services.definir_adresse_par_defaut(adresse)
            messages.success(request, "Adresse par défaut modifiée.")
        else:
            services.desactiver_adresse(adresse)
            messages.success(request, "Adresse retirée.")
    except ValueError as erreur:
        messages.error(request, str(erreur))
    return redirect("client_detail", pk=client.pk)


@login_required
def ajouter_contact(request, pk):
    client = get_object_or_404(Client, pk=pk)
    _exiger(request, "clients.gerer_ce_client", client)
    formulaire = ContactForm(request.POST or None)
    if request.method == "POST" and formulaire.is_valid():
        services.ajouter_contact(client, **formulaire.cleaned_data)
        messages.success(request, "Contact ajouté.")
        return redirect("client_detail", pk=client.pk)
    return render(
        request,
        "clients/formulaire.html",
        {"client": client, "formulaire": formulaire, "titre": "Nouveau contact"},
    )
