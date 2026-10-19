"""Gestion des modèles récurrents dans l'administration (décision 8.1), en attendant
un écran dédié. Réservée à la Direction et au Gestionnaire, limitée à leur entreprise."""

from django import forms
from django.contrib import admin

from entreprises.parametres import creneaux
from noyau.admin import AdminEntreprise

from .models import JourSemaine, LigneModeleRecurrent, ModeleRecurrent


class ModeleRecurrentForm(forms.ModelForm):
    """Champs remplacés : jours en cases à cocher, créneau parmi ceux de l'entreprise.

    Sans `Meta` : le modèle et les champs sont fixés par l'administration à chaque
    requête, quand l'entreprise courante est connue (les listes de clients et
    d'adresses ne peuvent pas être lues au chargement du module).
    """

    jours_semaine = forms.TypedMultipleChoiceField(
        label="Jours de livraison",
        choices=JourSemaine.choices,
        coerce=int,
        widget=forms.CheckboxSelectMultiple,
    )
    creneau = forms.ChoiceField(label="Créneau")


class LigneModeleRecurrentInline(admin.TabularInline):
    model = LigneModeleRecurrent
    fields = ["produit", "quantite"]
    extra = 3
    can_delete = False  # retirer un produit : quantité modifiée ou modèle désactivé
    verbose_name = "produit habituel"
    verbose_name_plural = "produits habituels"

    def has_add_permission(self, request, obj=None):
        return request.user.has_perm("commandes.change_modelerecurrent")

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm("commandes.change_modelerecurrent")

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm("commandes.view_modelerecurrent")


@admin.register(ModeleRecurrent)
class ModeleRecurrentAdmin(AdminEntreprise):
    form = ModeleRecurrentForm
    fields = ["client", "adresse", "jours_semaine", "creneau", "date_debut", "date_fin", "actif"]
    inlines = [LigneModeleRecurrentInline]
    list_display = ["__str__", "creneau", "date_debut", "date_fin", "actif"]
    list_filter = ["actif", "creneau"]
    search_fields = ["client__nom"]
    readonly_fields = []

    def get_form(self, request, obj=None, **kwargs):
        formulaire = super().get_form(request, obj, **kwargs)
        choix = [(c, c) for c in creneaux(request.user.entreprise)]
        formulaire.base_fields["creneau"].choices = choix
        return formulaire
