from django.apps import AppConfig


class ComptesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "comptes"
    verbose_name = "Comptes"

    def ready(self):
        # Enregistre les permissions `ecrans.*` (django-rules) dès le démarrage.
        from . import navigation  # noqa: F401
