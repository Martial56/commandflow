from django import forms

from noyau.forms import FormulaireStyle

from .models import Adresse, Client, ContactClient
from .services import CHAMPS_ADRESSE, CHAMPS_CLIENT, CHAMPS_CONTACT


class ClientForm(FormulaireStyle, forms.ModelForm):
    class Meta:
        model = Client
        fields = sorted(CHAMPS_CLIENT, key=list(f.name for f in Client._meta.fields).index)


class AdresseForm(FormulaireStyle, forms.ModelForm):
    class Meta:
        model = Adresse
        fields = sorted(CHAMPS_ADRESSE, key=list(f.name for f in Adresse._meta.fields).index)


class ContactForm(FormulaireStyle, forms.ModelForm):
    class Meta:
        model = ContactClient
        fields = ["nom", "fonction", "telephone"]


class ContactFacultatifForm(ContactForm):
    """Contact principal, facultatif à la création du client."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for champ in self.fields.values():
            champ.required = False

    def est_rempli(self):
        return any(self.cleaned_data.get(champ) for champ in CHAMPS_CONTACT)

    def clean(self):
        donnees = super().clean()
        if self.est_rempli() and not donnees.get("nom"):
            self.add_error("nom", "Indiquer le nom du contact.")
        return donnees


class ConfirmationDoublonForm(forms.Form):
    forcer_doublon = forms.BooleanField(label="Créer quand même ce client", required=False)
