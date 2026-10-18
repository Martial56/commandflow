from django.contrib import admin

from comptes.models import Role
from comptes.rules import a_le_role

_gere_les_referentiels = a_le_role(Role.DIRECTION, Role.GESTIONNAIRE)


class SiteAdmin(admin.AdminSite):
    """Administration ComandFlow.

    Ouverte au personnel (`is_staff`) et, sans ce statut, à la Direction et au
    Gestionnaire pour les référentiels de leur entreprise (décision 8.1). Chaque page
    reste soumise aux permissions de son modèle (django-rules).
    """

    site_header = "Administration ComandFlow"
    site_title = "ComandFlow"
    index_title = "Référentiels"

    def has_permission(self, request):
        utilisateur = request.user
        return utilisateur.is_active and (
            utilisateur.is_staff or _gere_les_referentiels(utilisateur)
        )
