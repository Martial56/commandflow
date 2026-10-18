from django.contrib.admin.apps import AdminConfig


class AdminComandflowConfig(AdminConfig):
    """Remplace le site d'administration par défaut par `config.admin.SiteAdmin`."""

    default_site = "config.admin.SiteAdmin"
