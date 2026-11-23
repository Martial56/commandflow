"""Formulaires de l'écran Paramètres (maquette 17)."""

from django import forms

from catalogue.models import Produit
from comptes.models import ROLES_DOUBLE_AUTHENTIFICATION
from comptes.services import ROLES_CREABLES
from noyau.forms import FormulaireStyle

HEURE = forms.TimeInput(attrs={"type": "time"}, format="%H:%M")


class FormulaireNouveauCompte(FormulaireStyle, forms.Form):
    prenom = forms.CharField(label="Prénom", max_length=150)
    nom = forms.CharField(label="Nom", max_length=150)
    telephone = forms.CharField(
        label="Téléphone",
        max_length=20,
        widget=forms.TextInput(attrs={"placeholder": "+2250700000000", "inputmode": "tel"}),
    )
    email = forms.EmailField(label="E-mail (facultatif)", required=False)
    role = forms.ChoiceField(label="Rôle")

    def __init__(self, *args, direction=False, **kwargs):
        super().__init__(*args, **kwargs)
        # Comptes Direction et Comptabilité : créés par la Direction seulement (8.6).
        self.fields["role"].choices = [
            (role.value, role.label)
            for role in ROLES_CREABLES
            if direction or role not in ROLES_DOUBLE_AUTHENTIFICATION
        ]


class FormulaireRegles(FormulaireStyle, forms.Form):
    generation = forms.TimeField(label="Génération des récurrentes", widget=HEURE)
    limite = forms.TimeField(label="Heure limite de confirmation", widget=HEURE)
    resume = forms.TimeField(label="Résumé quotidien", widget=HEURE)
    creneaux = forms.CharField(
        label="Créneaux de livraison (un par ligne)",
        widget=forms.Textarea(attrs={"rows": 4}),
    )

    def liste_creneaux(self):
        return [ligne.strip() for ligne in self.cleaned_data["creneaux"].splitlines()]


class FormulaireProduit(FormulaireStyle, forms.ModelForm):
    """Création ou modification d'un produit ; enregistrement par `catalogue.services`."""

    class Meta:
        model = Produit
        fields = ["code", "nom", "unite", "prix_reference", "taux_tva", "notes_preparation"]
        labels = {"prix_reference": "Prix TTC (FCFA)", "taux_tva": "TVA (%)"}
        widgets = {"notes_preparation": forms.Textarea(attrs={"rows": 2})}
