"""Véhicules et prestataires dans l'administration (décision 8.3), en attendant un écran
dédié. Réservés à la Direction et au Gestionnaire, limités à leur entreprise."""

from django.contrib import admin

from comptes.models import Role, Utilisateur
from noyau.admin import AdminEntreprise

from .models import Prestataire, Vehicule


@admin.register(Vehicule)
class VehiculeAdmin(AdminEntreprise):
    fields = ["immatriculation", "libelle", "actif"]
    list_display = ["immatriculation", "libelle", "actif"]
    list_filter = ["actif"]
    search_fields = ["immatriculation", "libelle"]


@admin.register(Prestataire)
class PrestataireAdmin(AdminEntreprise):
    fields = ["nom", "telephone", "compte", "actif"]
    list_display = ["nom", "telephone", "compte", "actif"]
    list_filter = ["actif"]
    search_fields = ["nom", "telephone"]

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "compte":
            # Comptes « Prestataire » de l'entreprise seulement : sans compte, le
            # gestionnaire saisit la réception à sa place (décision 8.3).
            kwargs["queryset"] = Utilisateur.objects.filter(
                entreprise=request.user.entreprise,
                is_active=True,
                groups__name=Role.PRESTATAIRE.label,
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)
