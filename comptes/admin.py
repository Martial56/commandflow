from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Utilisateur


@admin.register(Utilisateur)
class UtilisateurAdmin(UserAdmin):
    list_display = [
        "username",
        "get_full_name",
        "telephone",
        "entreprise",
        "role_principal",
        "is_active",
    ]
    list_filter = ["entreprise", "role_principal", "is_active", "groups"]
    search_fields = ["username", "first_name", "last_name", "telephone", "email"]
    fieldsets = UserAdmin.fieldsets + (
        ("ComandFlow", {"fields": ["entreprise", "telephone", "role_principal"]}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("ComandFlow", {"fields": ["entreprise", "telephone", "role_principal"]}),
    )

    def has_delete_permission(self, request, obj=None):
        # Désactivation (is_active) au lieu de suppression.
        return False
