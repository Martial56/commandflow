import re
from decimal import Decimal, InvalidOperation

from django import forms
from django.db.models import Q

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

    def __init__(self, *args, entreprise, client_fixe=None, **kwargs):
        """`client_fixe` : modification d'une commande existante (le client ne change pas)."""
        super().__init__(*args, **kwargs)
        self.client_fixe = client_fixe
        self.fields["creneau"].choices = [(c, c) for c in creneaux(entreprise)]
        if client_fixe is not None:
            del self.fields["client"]
            client = client_fixe
        else:
            client = trouver_client(self.data.get("client") or self.initial.get("client"))
        if client is not None:
            self.fields["adresse"].queryset = client.adresses.filter(actif=True)
            self.fields["adresse"].initial = client.adresse_par_defaut

    def clean_client(self):
        client = trouver_client(self.cleaned_data["client"])
        if client is None:
            raise forms.ValidationError("Choisir un client actif dans la liste.")
        return client


class DemandeModificationForm(EnTeteCommandeForm):
    """Demande de modification d'une commande confirmée (validée par un responsable)."""

    motif = forms.CharField(
        label="Motif de la demande",
        widget=forms.TextInput(attrs={"placeholder": "Demande du client par WhatsApp"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for champ in ("canal", "urgente", "justificatif"):
            del self.fields[champ]


class MotifForm(FormulaireStyle, forms.Form):
    motif = forms.CharField(label="Motif", max_length=500)


class NoteConfirmationForm(FormulaireStyle, forms.Form):
    note = forms.CharField(
        label="Note de confirmation (facultative)",
        required=False,
        max_length=500,
        widget=forms.TextInput(attrs={"placeholder": "Confirmé par téléphone avec M. Koffi"}),
    )


def lire_quantite(texte):
    """« 120 », « 2,5 » → Decimal ; None si vide ou illisible."""
    texte = (texte or "").replace(" ", "").replace(",", ".")
    try:
        return Decimal(texte) if texte else None
    except InvalidOperation:
        return None


class LigneSaisie:
    """Ligne du tableau de saisie (index, produit choisi, quantité, montant affiché)."""

    def __init__(self, index, produit=None, quantite_texte="", erreur="", prix_fige=None):
        self.index = index
        self.produit = produit
        self.quantite_texte = quantite_texte
        self.quantite = lire_quantite(quantite_texte)
        self.erreur = erreur
        self.prix_fige = prix_fige

    @property
    def prix_unitaire(self):
        """Prix figé d'une ligne existante, sinon prix actuel du catalogue."""
        if self.prix_fige is not None:
            return self.prix_fige
        return self.produit.prix_reference if self.produit is not None else None

    @property
    def montant(self):
        if self.prix_unitaire is None or self.quantite is None:
            return None
        return arrondir_fcfa(self.quantite * self.prix_unitaire)


def lire_lignes(donnees, prix_figes=None):
    """Lignes postées (`produit_<i>`, `quantite_<i>`), dans l'ordre d'affichage.

    `prix_figes` : {produit_id: prix} des lignes existantes d'une commande modifiée
    (leur produit reste accepté même s'il a été retiré du catalogue depuis).
    """
    prix_figes = prix_figes or {}
    index = sorted(
        {cle.split("_", 1)[1] for cle in donnees if re.fullmatch(r"(produit|quantite)_\d+", cle)},
        key=int,
    )
    demandes = [donnees[f"produit_{i}"] for i in index if donnees.get(f"produit_{i}", "").isdigit()]
    produits = Produit.objects.filter(Q(actif=True) | Q(pk__in=prix_figes)).in_bulk(demandes)
    lignes = []
    for i in index:
        identifiant = donnees.get(f"produit_{i}", "")
        produit = produits.get(int(identifiant)) if identifiant.isdigit() else None
        prix = prix_figes.get(produit.pk) if produit is not None else None
        lignes.append(LigneSaisie(i, produit, donnees.get(f"quantite_{i}", ""), prix_fige=prix))
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
