from django.db import models


class Entreprise(models.Model):
    """Entreprise cliente de ComandFlow (tenant)."""

    nom = models.CharField("nom", max_length=150)
    slug = models.SlugField("identifiant", max_length=60, unique=True)
    ncc = models.CharField("numéro de compte contribuable", max_length=30, blank=True)
    telephone = models.CharField("téléphone", max_length=20, blank=True)
    adresse = models.TextField("adresse", blank=True)
    devise = models.CharField("devise", max_length=3, default="XOF")
    options = models.JSONField(
        "options",
        default=dict,
        blank=True,
        help_text="Preuves activées par catégorie de client, GPS, double validation…",
    )

    class Meta:
        verbose_name = "entreprise"
        verbose_name_plural = "entreprises"
        ordering = ["nom"]

    def __str__(self):
        return self.nom
