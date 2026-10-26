"""Production groupée (§9 du cahier des charges, §3.4 et §8.2 de modele-donnees.md).

Un plan regroupe, pour une date de livraison, les commandes confirmées ; un besoin
par produit compare le commandé, l'objectif (« à produire ») et le cumul produit.
Toute écriture passe par `production.services`.
"""

from decimal import Decimal

import pghistory
import pgtrigger
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django_fsm import FSMField, FSMModelMixin, transition

from noyau.models import ModeleEntreprise, ModeleTrace

QUANTITE = {"max_digits": 10, "decimal_places": 3}
ZERO = Decimal("0")


class StatutPlan(models.TextChoices):
    BROUILLON = "brouillon", "Brouillon"
    VALIDE = "valide", "Validé"
    CLOTURE = "cloture", "Clôturé"


@pghistory.track(model_name="HistoriquePlanProduction")
class PlanProduction(FSMModelMixin, ModeleEntreprise, ModeleTrace):
    """Plan de production d'une date de livraison (un plan par jour, décision 8.2)."""

    date_livraison = models.DateField("date de livraison")
    # Vide : plan de la journée entière. Le découpage par créneau est prévu par le modèle.
    creneau = models.CharField("créneau", max_length=20, blank=True, default="")
    statut = FSMField(
        "statut",
        max_length=20,
        choices=StatutPlan.choices,
        default=StatutPlan.BROUILLON,
        protected=True,
    )
    valide_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="validé par",
    )
    valide_le = models.DateTimeField("validé le", null=True, blank=True)
    cloture_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="clôturé par",
    )
    cloture_le = models.DateTimeField("clôturé le", null=True, blank=True)
    # Alerte « le plan a changé depuis sa validation » (décision 8.2), tenue par les services.
    ajouts_apres_validation = models.PositiveIntegerField(
        "commandes ajoutées après validation", default=0
    )
    changements_apres_validation = models.PositiveIntegerField(
        "commandes modifiées ou annulées après validation", default=0
    )

    class Meta:
        verbose_name = "plan de production"
        verbose_name_plural = "plans de production"
        constraints = [
            models.UniqueConstraint(
                fields=["entreprise", "date_livraison", "creneau"],
                name="plan_unique_par_date_et_creneau",
            ),
        ]

    def __str__(self):
        creneau = f" · {self.creneau}" if self.creneau else ""
        return f"Plan du {self.date_livraison:%d/%m/%Y}{creneau}"

    def full_clean(self, exclude=None, **kwargs):
        # Statut protégé : contrôlé par les transitions, pas par la validation de Django.
        super().full_clean(exclude={*(exclude or ()), "statut"}, **kwargs)

    def a_des_besoins(self):
        return self.besoins.filter(quantite_commandee__gt=0).exists()

    @transition(
        field=statut,
        source=StatutPlan.BROUILLON,
        target=StatutPlan.VALIDE,
        conditions=[a_des_besoins],
    )
    def valider(self, par):
        self.valide_par = par
        self.valide_le = timezone.now()

    @transition(field=statut, source=StatutPlan.VALIDE, target=StatutPlan.CLOTURE)
    def cloturer(self, par):
        self.cloture_par = par
        self.cloture_le = timezone.now()


@pghistory.track(model_name="HistoriqueBesoinProduit")
class BesoinProduit(ModeleEntreprise, ModeleTrace):
    """Besoin d'un produit dans un plan.

    `quantite_commandee`, `quantite_produite` et `quantite_reservee` sont calculées par
    les services ; `quantite_a_produire` est l'objectif fixé par le responsable.
    """

    plan = models.ForeignKey(
        PlanProduction, on_delete=models.PROTECT, related_name="besoins", verbose_name="plan"
    )
    produit = models.ForeignKey(
        "catalogue.Produit", on_delete=models.PROTECT, related_name="+", verbose_name="produit"
    )
    quantite_commandee = models.DecimalField("commandé", default=ZERO, **QUANTITE)
    quantite_a_produire = models.DecimalField("à produire", default=ZERO, **QUANTITE)
    quantite_produite = models.DecimalField("produit", default=ZERO, **QUANTITE)
    quantite_reservee = models.DecimalField("réservé", default=ZERO, **QUANTITE)

    class Meta:
        verbose_name = "besoin de production"
        verbose_name_plural = "besoins de production"
        constraints = [
            models.UniqueConstraint(fields=["plan", "produit"], name="besoin_unique_par_produit"),
            models.CheckConstraint(
                condition=Q(quantite_commandee__gte=0)
                & Q(quantite_a_produire__gte=0)
                & Q(quantite_produite__gte=0)
                & Q(quantite_reservee__gte=0),
                name="besoin_quantites_positives",
            ),
        ]

    def __str__(self):
        return f"{self.produit} — {self.plan}"

    # --- Quantités dérivées (décision 8.2) ---

    @property
    def marge(self):
        return self.quantite_a_produire - self.quantite_commandee

    @property
    def quantite_manquante(self):
        return max(self.quantite_a_produire - self.quantite_produite, ZERO)

    @property
    def quantite_disponible(self):
        return self.quantite_produite - self.quantite_reservee

    @property
    def complet(self):
        return self.quantite_a_produire > 0 and self.quantite_manquante == 0


@pghistory.track(model_name="HistoriqueSaisieProduction")
@pgtrigger.register(
    # Une saisie n'est jamais modifiée (§3.4) : seule son annulation est possible.
    pgtrigger.ReadOnly(
        name="saisie_production_non_modifiable",
        fields=["besoin", "lot", "quantite", "saisie_le", "saisie_par", "commentaire"],
    ),
)
class SaisieProduction(ModeleEntreprise, ModeleTrace):
    """Fournée déclarée par l'atelier ; s'ajoute aux précédentes sans les remplacer."""

    besoin = models.ForeignKey(
        BesoinProduit, on_delete=models.PROTECT, related_name="saisies", verbose_name="besoin"
    )
    lot = models.CharField("lot", max_length=80)
    quantite = models.DecimalField("quantité", **QUANTITE)
    saisie_le = models.DateTimeField("saisie le", default=timezone.now)
    saisie_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="saisie par",
    )
    commentaire = models.CharField("commentaire", max_length=255, blank=True)
    annulee = models.BooleanField("annulée", default=False)
    motif_annulation = models.CharField("motif d'annulation", max_length=255, blank=True)
    annulee_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="annulée par",
    )
    annulee_le = models.DateTimeField("annulée le", null=True, blank=True)

    class Meta:
        verbose_name = "saisie de production"
        verbose_name_plural = "saisies de production"
        ordering = ["saisie_le", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(quantite__gt=0), name="saisie_quantite_positive"),
            models.CheckConstraint(
                condition=Q(annulee=False) | ~Q(motif_annulation=""),
                name="saisie_annulee_avec_motif",
            ),
        ]

    def __str__(self):
        return f"{self.lot} · {self.quantite}"
