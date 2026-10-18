from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

from noyau.models import ModeleEntreprise, ModeleTrace


class Unite(models.TextChoices):
    PIECE = "piece", "pièce"
    KG = "kg", "kg"
    CARTON = "carton", "carton"


# Unités vendues au poids : seules à accepter des quantités décimales (décision 8.1).
UNITES_DECIMALES = {Unite.KG}


class Produit(ModeleEntreprise, ModeleTrace):
    """Produit du catalogue. Désactivé (`actif=False`) au lieu d'être supprimé.

    `prix_reference` est copié sur chaque ligne de commande à la création (prix figé).
    """

    code = models.CharField("code", max_length=30)
    nom = models.CharField("nom", max_length=150)
    unite = models.CharField("unité", max_length=10, choices=Unite.choices, default=Unite.PIECE)
    prix_reference = models.DecimalField(
        "prix de référence (FCFA)",
        max_digits=14,
        decimal_places=0,
        validators=[MinValueValidator(0)],
    )
    notes_preparation = models.TextField("notes de préparation", blank=True)
    actif = models.BooleanField("actif", default=True)

    class Meta:
        verbose_name = "produit"
        verbose_name_plural = "produits"
        ordering = ["nom"]
        constraints = [
            models.UniqueConstraint(
                fields=["entreprise", "code"], name="produit_code_unique_par_entreprise"
            ),
            models.CheckConstraint(
                condition=Q(prix_reference__gte=0), name="produit_prix_reference_positif"
            ),
        ]

    def __str__(self):
        return f"{self.nom} ({self.code})"

    @property
    def quantites_decimales(self):
        return self.unite in UNITES_DECIMALES

    def accepte_quantite(self, quantite):
        """Vrai si la quantité est positive, et entière sauf pour une unité au poids."""
        quantite = Decimal(quantite)
        if quantite <= 0:
            return False
        return self.quantites_decimales or quantite == quantite.to_integral_value()
