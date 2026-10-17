from django.contrib.auth.models import AbstractUser
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q

valider_telephone = RegexValidator(
    regex=r"^\+[1-9]\d{7,14}$",
    message="Format international attendu, par exemple +2250700000000.",
)


class Role(models.TextChoices):
    """Profils de départ du §5 ; chaque rôle correspond à un groupe Django."""

    DIRECTION = "direction", "Direction"
    GESTIONNAIRE = "gestionnaire", "Gestionnaire"
    COMMERCIAL = "commercial", "Commercial"
    PRODUCTION = "production", "Production"
    PREPARATION = "preparation", "Préparation"
    CHAUFFEUR = "chauffeur", "Chauffeur"
    FACTURATION = "facturation", "Facturation"
    COMPTABILITE = "comptabilite", "Comptabilité"
    PRESTATAIRE = "prestataire", "Prestataire"


class Utilisateur(AbstractUser):
    """Compte individuel. Désactivé (is_active=False) au lieu d'être supprimé."""

    entreprise = models.ForeignKey(
        "entreprises.Entreprise",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="utilisateurs",
        verbose_name="entreprise",
        help_text="Vide uniquement pour un superutilisateur de la plateforme.",
    )
    telephone = models.CharField(
        "téléphone", max_length=20, unique=True, validators=[valider_telephone]
    )
    role_principal = models.CharField(
        "rôle principal",
        max_length=20,
        choices=Role.choices,
        blank=True,
        help_text=(
            "Rôle affiché ; les droits viennent des groupes "
            "(un utilisateur peut en avoir plusieurs)."
        ),
    )

    REQUIRED_FIELDS = ["telephone"]

    class Meta:
        verbose_name = "utilisateur"
        verbose_name_plural = "utilisateurs"
        constraints = [
            models.CheckConstraint(
                condition=Q(entreprise__isnull=False) | Q(is_superuser=True),
                name="utilisateur_entreprise_obligatoire_sauf_superutilisateur",
            ),
        ]

    def __str__(self):
        return self.get_full_name() or self.username
