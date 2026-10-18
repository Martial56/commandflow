"""Base de l'administration Django des modèles métier.

En attendant des écrans dédiés (décision 8.1), la Direction et le Gestionnaire gèrent
certains référentiels dans l'administration Django. Les données y restent limitées à
l'entreprise de l'utilisateur (gestionnaire `objects` filtré par le middleware).
"""

from django.contrib import admin


class AdminEntreprise(admin.ModelAdmin):
    """Administration d'un `ModeleEntreprise` : pas de suppression, entreprise implicite.

    Un superutilisateur de la plateforme (sans entreprise) ne voit aucune donnée métier.
    Les sous-classes enregistrent via les services de leur app (`save_model`).
    """

    exclude = ["entreprise"]
    readonly_fields = ["cree_le", "cree_par", "modifie_le", "modifie_par"]

    @staticmethod
    def _a_une_entreprise(request):
        return getattr(request.user, "entreprise_id", None) is not None

    def has_module_permission(self, request):
        return self._a_une_entreprise(request) and super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        return self._a_une_entreprise(request) and super().has_view_permission(request, obj)

    def has_add_permission(self, request):
        return self._a_une_entreprise(request) and super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        return self._a_une_entreprise(request) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        # Aucune suppression physique.
        return False
