from django.apps import AppConfig


class NoyauTestsConfig(AppConfig):
    """App installée uniquement par config.settings.test : modèles concrets de test."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "noyau.tests"
    label = "noyau_tests"
