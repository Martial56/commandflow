from django.db import models

from noyau.models import ModeleEntreprise, ModeleTrace


class ObjetTest(ModeleEntreprise, ModeleTrace):
    """Modèle métier minimal pour tester les classes abstraites du noyau."""

    nom = models.CharField(max_length=50)
