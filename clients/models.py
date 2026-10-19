import re
import unicodedata

from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q

from noyau.models import ModeleEntreprise, ModeleTrace

valider_telephone = RegexValidator(
    regex=r"^\+?[0-9 .\-]{8,20}$",
    message="Numéro de téléphone invalide (chiffres, espaces, points, tirets ; + facultatif).",
)


def normaliser_telephone(valeur):
    """« +225 07 00-00.00 01 » → « +2250700000001 » (comparaison des doublons)."""
    return re.sub(r"[ .\-]", "", valeur or "")


def normaliser_nom(valeur):
    """Nom sans accents, sans casse ni espaces multiples (comparaison des doublons)."""
    sans_accents = unicodedata.normalize("NFKD", valeur or "").encode("ascii", "ignore")
    return " ".join(sans_accents.decode().casefold().split())


class Categorie(models.TextChoices):
    RESTAURANT = "restaurant", "Restaurant"
    SUPERMARCHE = "supermarche", "Supermarché"
    PARTICULIER = "particulier", "Particulier"
    REVENDEUR = "revendeur", "Revendeur"
    ENTREPRISE = "entreprise", "Entreprise"
    EVENEMENT = "evenement", "Événement"


class ConditionsPaiement(models.TextChoices):
    COMPTANT = "comptant", "Comptant"
    FIN_DE_MOIS = "fin_de_mois", "Fin de mois"
    X_JOURS = "x_jours", "À X jours"


class Client(ModeleEntreprise, ModeleTrace):
    """Fiche client (§7.1). Désactivée (`actif=False`) au lieu d'être supprimée."""

    code = models.CharField("code", max_length=20, editable=False)
    nom = models.CharField("nom", max_length=150)
    categorie = models.CharField("catégorie", max_length=20, choices=Categorie.choices)
    telephone = models.CharField("téléphone", max_length=20, validators=[valider_telephone])
    ncc = models.CharField("N° compte contribuable", max_length=30, blank=True)
    conditions_paiement = models.CharField(
        "conditions de paiement",
        max_length=20,
        choices=ConditionsPaiement.choices,
        default=ConditionsPaiement.COMPTANT,
    )
    delai_paiement_jours = models.PositiveSmallIntegerField(
        "délai de paiement (jours)", null=True, blank=True
    )
    notes = models.TextField("notes", blank=True)
    actif = models.BooleanField("actif", default=True)

    class Meta:
        verbose_name = "client"
        verbose_name_plural = "clients"
        ordering = ["nom"]
        constraints = [
            models.UniqueConstraint(
                fields=["entreprise", "code"], name="client_code_unique_par_entreprise"
            ),
            models.CheckConstraint(
                # isnull explicite : en SQL, « NULL > 0 » n'est pas faux et passerait le CHECK.
                condition=~Q(conditions_paiement="x_jours")
                | Q(delai_paiement_jours__isnull=False, delai_paiement_jours__gt=0),
                name="client_delai_obligatoire_a_x_jours",
                violation_error_message="Indiquer le délai de paiement en jours.",
            ),
        ]

    def __str__(self):
        return self.nom

    @property
    def adresse_par_defaut(self):
        return self.adresses.filter(par_defaut=True, actif=True).first()

    def libelle_conditions(self):
        if self.conditions_paiement == ConditionsPaiement.X_JOURS:
            return f"À {self.delai_paiement_jours} jours"
        return self.get_conditions_paiement_display()


class ContactClient(ModeleEntreprise, ModeleTrace):
    client = models.ForeignKey(
        Client, on_delete=models.PROTECT, related_name="contacts", verbose_name="client"
    )
    nom = models.CharField("nom", max_length=150)
    fonction = models.CharField("fonction", max_length=100, blank=True)
    telephone = models.CharField(
        "téléphone", max_length=20, blank=True, validators=[valider_telephone]
    )

    class Meta:
        verbose_name = "contact"
        verbose_name_plural = "contacts"
        ordering = ["nom"]

    def __str__(self):
        return self.nom


class Adresse(ModeleEntreprise, ModeleTrace):
    """Adresse de livraison. Une seule adresse par défaut (active) par client."""

    client = models.ForeignKey(
        Client, on_delete=models.PROTECT, related_name="adresses", verbose_name="client"
    )
    libelle = models.CharField("libellé", max_length=100)
    adresse = models.CharField("adresse", max_length=255)
    commune = models.CharField("commune", max_length=100)
    repere = models.CharField("repère", max_length=255, blank=True)
    latitude = models.DecimalField(
        "latitude", max_digits=9, decimal_places=6, null=True, blank=True
    )
    longitude = models.DecimalField(
        "longitude", max_digits=9, decimal_places=6, null=True, blank=True
    )
    consignes = models.TextField("consignes", blank=True)
    par_defaut = models.BooleanField("adresse par défaut", default=False)
    actif = models.BooleanField("active", default=True)

    class Meta:
        verbose_name = "adresse"
        verbose_name_plural = "adresses"
        ordering = ["-par_defaut", "libelle"]
        constraints = [
            models.UniqueConstraint(
                fields=["client"],
                condition=Q(par_defaut=True),
                name="adresse_une_seule_par_defaut",
            ),
            models.CheckConstraint(
                condition=Q(par_defaut=False) | Q(actif=True),
                name="adresse_par_defaut_active",
            ),
        ]

    def __str__(self):
        return f"{self.libelle} — {self.adresse}, {self.commune}"

    @property
    def gps_enregistre(self):
        return self.latitude is not None and self.longitude is not None
