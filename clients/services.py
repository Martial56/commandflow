from django.db import transaction

from noyau.contexte import entreprise_obligatoire
from noyau.services import prochain_numero

from .models import Adresse, Client, ContactClient, normaliser_nom, normaliser_telephone

CHAMPS_CLIENT = {
    "nom",
    "categorie",
    "telephone",
    "ncc",
    "conditions_paiement",
    "delai_paiement_jours",
    "notes",
}
CHAMPS_ADRESSE = {"libelle", "adresse", "commune", "repere", "latitude", "longitude", "consignes"}
CHAMPS_CONTACT = {"nom", "fonction", "telephone"}


class DoublonPotentiel(Exception):
    """Un client de même nom et même téléphone existe déjà (décision 8.1 : avertissement)."""

    def __init__(self, clients):
        self.clients = list(clients)
        noms = ", ".join(f"{c.nom} ({c.code})" for c in self.clients)
        super().__init__(f"Client déjà enregistré avec ce nom et ce téléphone : {noms}.")


def doublons_potentiels(nom, telephone, exclure=None):
    """Clients de l'entreprise courante ayant le même nom et le même téléphone."""
    nom, telephone = normaliser_nom(nom), normaliser_telephone(telephone)
    candidats = Client.objects.exclude(pk=getattr(exclure, "pk", None))
    return [
        c
        for c in candidats.only("pk", "code", "nom", "telephone")
        if normaliser_nom(c.nom) == nom and normaliser_telephone(c.telephone) == telephone
    ]


def _verifier_champs(donnees, autorises):
    inconnus = set(donnees) - autorises
    if inconnus:
        raise ValueError(f"Champs non modifiables : {', '.join(sorted(inconnus))}")


@transaction.atomic
def creer_client(*, adresse=None, contact=None, forcer_doublon=False, **donnees):
    """Crée un client (code CLI-000123), avec son adresse par défaut et son contact.

    Lève `DoublonPotentiel` si un client de même nom et même téléphone existe, sauf
    si `forcer_doublon` est vrai (l'utilisateur a confirmé).
    """
    _verifier_champs(donnees, CHAMPS_CLIENT)
    entreprise = entreprise_obligatoire()
    if not forcer_doublon:
        doublons = doublons_potentiels(donnees.get("nom"), donnees.get("telephone"))
        if doublons:
            raise DoublonPotentiel(doublons)

    client = Client(entreprise=entreprise, **donnees)
    client.full_clean(exclude=["code"])
    client.code = prochain_numero("CLI", annuel=False)
    client.save()
    if adresse:
        ajouter_adresse(client, par_defaut=True, **adresse)
    if contact:
        ajouter_contact(client, **contact)
    return client


@transaction.atomic
def modifier_client(client, **donnees):
    _verifier_champs(donnees, CHAMPS_CLIENT)
    client = Client.objects.select_for_update().get(pk=client.pk)
    for champ, valeur in donnees.items():
        setattr(client, champ, valeur)
    client.full_clean()
    client.save()
    return client


@transaction.atomic
def desactiver_client(client):
    """Le client n'est plus proposé pour de nouvelles commandes ; son historique reste."""
    return _changer_activite(client, actif=False)


@transaction.atomic
def reactiver_client(client):
    return _changer_activite(client, actif=True)


def _changer_activite(client, *, actif):
    client = Client.objects.select_for_update().get(pk=client.pk)
    client.actif = actif
    client.save(update_fields=["actif"])
    return client


@transaction.atomic
def ajouter_adresse(client, *, par_defaut=False, **donnees):
    """Ajoute une adresse ; la première adresse d'un client devient l'adresse par défaut."""
    _verifier_champs(donnees, CHAMPS_ADRESSE)
    client = Client.objects.select_for_update().get(pk=client.pk)
    premiere = not client.adresses.filter(actif=True).exists()
    adresse = Adresse(entreprise=client.entreprise, client=client, **donnees)
    adresse.full_clean()
    adresse.save()
    if par_defaut or premiere:
        definir_adresse_par_defaut(adresse)
    return adresse


@transaction.atomic
def modifier_adresse(adresse, **donnees):
    _verifier_champs(donnees, CHAMPS_ADRESSE)
    adresse = Adresse.objects.select_for_update().get(pk=adresse.pk)
    for champ, valeur in donnees.items():
        setattr(adresse, champ, valeur)
    adresse.full_clean()
    adresse.save()
    return adresse


@transaction.atomic
def definir_adresse_par_defaut(adresse):
    """L'adresse devient l'adresse par défaut ; l'ancienne perd ce statut."""
    adresse = Adresse.objects.select_for_update().get(pk=adresse.pk)
    if not adresse.actif:
        raise ValueError("Une adresse désactivée ne peut pas être l'adresse par défaut.")
    for ancienne in Adresse.objects.filter(client_id=adresse.client_id, par_defaut=True).exclude(
        pk=adresse.pk
    ):
        ancienne.par_defaut = False
        ancienne.save(update_fields=["par_defaut"])
    adresse.par_defaut = True
    adresse.save(update_fields=["par_defaut"])
    return adresse


@transaction.atomic
def desactiver_adresse(adresse):
    """Retire une adresse. L'adresse par défaut doit d'abord être remplacée."""
    adresse = Adresse.objects.select_for_update().get(pk=adresse.pk)
    if adresse.par_defaut:
        raise ValueError("Choisir une autre adresse par défaut avant de désactiver celle-ci.")
    adresse.actif = False
    adresse.save(update_fields=["actif"])
    return adresse


@transaction.atomic
def ajouter_contact(client, **donnees):
    _verifier_champs(donnees, CHAMPS_CONTACT)
    contact = ContactClient(entreprise=client.entreprise, client=client, **donnees)
    contact.full_clean()
    contact.save()
    return contact
