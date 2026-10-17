from django.contrib import admin

from .models import Entreprise


@admin.register(Entreprise)
class EntrepriseAdmin(admin.ModelAdmin):
    list_display = ["nom", "slug", "ncc", "telephone", "devise"]
    search_fields = ["nom", "slug", "ncc"]
    prepopulated_fields = {"slug": ["nom"]}

    def has_delete_permission(self, request, obj=None):
        # Aucune suppression physique.
        return False
