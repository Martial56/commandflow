from django.contrib import admin, messages

from noyau.admin import AdminEntreprise

from . import services
from .models import Produit


@admin.register(Produit)
class ProduitAdmin(AdminEntreprise):
    list_display = ["code", "nom", "unite", "prix_reference", "actif"]
    list_filter = ["actif", "unite"]
    search_fields = ["code", "nom"]
    fields = ["code", "nom", "unite", "prix_reference", "notes_preparation", "actif"]
    readonly_fields = ["actif"]
    actions = ["desactiver", "reactiver"]

    def save_model(self, request, obj, form, change):
        donnees = {champ: form.cleaned_data[champ] for champ in services.CHAMPS_MODIFIABLES}
        if change:
            produit = services.modifier_produit(obj, **donnees)
        else:
            produit = services.creer_produit(**donnees)
        obj.pk = produit.pk

    @admin.action(description="Désactiver les produits sélectionnés")
    def desactiver(self, request, queryset):
        for produit in queryset:
            services.desactiver_produit(produit)
        self.message_user(request, f"{queryset.count()} produit(s) désactivé(s).", messages.SUCCESS)

    @admin.action(description="Réactiver les produits sélectionnés")
    def reactiver(self, request, queryset):
        for produit in queryset:
            services.reactiver_produit(produit)
        self.message_user(request, f"{queryset.count()} produit(s) réactivé(s).", messages.SUCCESS)
