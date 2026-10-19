import re
from decimal import Decimal, InvalidOperation

from django import forms

from catalogue.models import Produit
from clients.models import Adresse, Client
from entreprises.parametres import creneaux
from noyau.forms import FormulaireStyle

from .models import Canal, arrondir_fcfa


def libelle_client(client):
    """Texte proposé dans la saisie du client (maquette 1) : « Nom — CLI-000042 »."""
    return f"{client.nom} — {client.code}"


def trouver_client(texte):
    """Client actif désigné par « Nom — CLI-… », par son code ou par son nom exact."""
    texte = (texte or "").strip()
    code = re.search(r"CLI-\d+", texte)
    clients = Client.objects.filter(actif=True)
    if code:
        return clients.filter(code=code.group()).first()
    return clients.filter(nom__iexact=texte).first() if texte else None


class EnTeteCommandeForm(FormulaireStyle, forms.Form):
    client = forms.CharField(
        label="Client",
        widget=forms.TextInput(attrs={"list": "clients-actifs", "autocomplete": "off"}),
    )
    canal = forms.ChoiceField(label="Canal de réception", choices=Canal.choices)
    adresse = forms.ModelChoiceField(
        label="Adresse de livraison", queryset=Adresse.toutes_entreprises.none(), empty_label=None
    )
    date_livraison = forms.DateField(
        label="Date de livraison", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
    )
    creneau = forms.ChoiceField(label="Créneau")
    urgente = forms.BooleanField(label="Commande urgente", required=False)
    instructions = forms.CharField(
        label="Instructions de livraison", required=False, widget=forms.Textarea
    )
    justificatif = forms.FileField(
        label="Bon de commande, photo ou capture WhatsApp",
        required=False,
        widget=forms.ClearableFileInput(attrs={"accept": "image/*,application/pdf"}),
    )

    def __init__(self, *args, entreprise, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["creneau"].choices = [(c, c) for c in creneaux(entreprise)]
        client = trouver_client(self.data.get("client") or self.initial.get("client"))
        if client is not None:
            self.fields["adresse"].queryset = client.adresses.filter(actif=True)
            self.fields["adresse"].initial = client.adresse_par_defaut

    def clean_client(self):
        client = trouver_client(self.cleaned_data["client"])
        if client is None:
            raise forms.ValidationError("Choisir un client actif dans la liste.")
        return client


def lire_quantite(texte):
    """« 120 », « 2,5 » → Decimal ; None si vide ou illisible."""
    texte = (texte or "").replace(" ", "").replace(",", ".")
    try:
        return Decimal(texte) if texte else None
    except InvalidOperation:
        return None


class LigneSaisie:
    """Ligne du tableau de saisie (index, produit choisi, quantité, montant affiché)."""

    def __init__(self, index, produit=None, quantite_texte="", erreur=""):
        self.index = index
        self.produit = produit
        self.quantite_texte = quantite_texte
        self.quantite = lire_quantite(quantite_texte)
        self.erreur = erreur

    @property
    def montant(self):
        if self.produit is None or self.quantite is None:
            return None
        return arrondir_fcfa(self.quantite * self.produit.prix_reference)


def lire_lignes(donnees):
    """Lignes postées (`produit_<i>`, `quantite_<i>`), dans l'ordre d'affichage."""
    index = sorted(
        {cle.split("_", 1)[1] for cle in donnees if re.fullmatch(r"(produit|quantite)_\d+", cle)},
        key=int,
    )
    produits = Produit.objects.filter(actif=True).in_bulk(
        [donnees[f"produit_{i}"] for i in index if donnees.get(f"produit_{i}", "").isdigit()]
    )
    lignes = []
    for i in index:
        identifiant = donnees.get(f"produit_{i}", "")
        produit = produits.get(int(identifiant)) if identifiant.isdigit() else None
        lignes.append(LigneSaisie(i, produit, donnees.get(f"quantite_{i}", "")))
    return lignes


def valider_lignes(lignes):
    """Vérifie les lignes ; renvoie [(produit, quantité)] et marque les erreurs sur les lignes."""
    valides = []
    for ligne in lignes:
        if ligne.produit is None and not ligne.quantite_texte:
            continue  # ligne laissée vide
        if ligne.produit is None:
            ligne.erreur = "Choisir un produit."
        elif ligne.quantite is None:
            ligne.erreur = "Indiquer la quantité."
        elif not ligne.produit.accepte_quantite(ligne.quantite):
            ligne.erreur = (
                "Quantité positive attendue."
                if ligne.produit.quantites_decimales
                else "Quantité entière et positive attendue."
            )
        else:
            valides.append((ligne.produit, ligne.quantite))
    return valides
