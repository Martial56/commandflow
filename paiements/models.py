"""Paiements et affectations (§15 du cahier des charges ; §3.6 de modele-donnees.md,
décisions 8.5).

Un paiement d'un client peut régler une ou plusieurs de ses factures ; la part non affectée
reste en avance du client. Le solde et le statut de paiement d'une facture sont calculés à
partir des affectations des paiements non annulés, jamais saisis. Toute écriture passe par
`paiements.services`.
"""

import pghistory
from django.conf import settings
from django.db import models
from django.db.models import Q

from noyau.models import ModeleEntreprise, ModeleTrace

MONTANT = {"max_digits": 14, "decimal_places": 0}


class ModePaiement(models.TextChoices):
    MOBILE_MONEY = "mobile_money", "Mobile Money"
    VIREMENT = "virement", "Virement"
    CHEQUE = "cheque", "Chèque"
    ESPECES = "especes", "Espèces"
    AUTRE = "autre", "Autre"


@pghistory.track(model_name="HistoriquePaiement")
class Paiement(ModeleEntreprise, ModeleTrace):
    """Règlement reçu d'un client. Annulé (avec motif) au lieu d'être supprimé (§3.6)."""

    client = models.ForeignKey(
        "clients.Client", on_delete=models.PROTECT, related_name="paiements", verbose_name="client"
    )
    montant = models.DecimalField("montant (FCFA)", **MONTANT)
    date = models.DateField("date")
    mode = models.CharField("mode", max_length=20, choices=ModePaiement.choices)
    reference = models.CharField("référence de transaction", max_length=120, blank=True)
    justificatif = models.ForeignKey(
        "noyau.PieceJointe",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="justificatif",
    )
    annule = models.BooleanField("annulé", default=False)
    motif_annulation = models.CharField("motif d'annulation", max_length=255, blank=True)
    annule_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="annulé par",
    )
    annule_le = models.DateTimeField("annulé le", null=True, blank=True)

    class Meta:
        verbose_name = "paiement"
        verbose_name_plural = "paiements"
        ordering = ["-date", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(montant__gt=0), name="paiement_montant_positif"),
            models.CheckConstraint(
                condition=Q(annule=False) | ~Q(motif_annulation=""),
                name="paiement_annulation_motivee",
            ),
        ]

    def __str__(self):
        return f"{self.get_mode_display()} {self.montant} FCFA · {self.client}"


@pghistory.track(model_name="HistoriqueAffectationPaiement")
class AffectationPaiement(ModeleEntreprise, ModeleTrace):
    """Part d'un paiement affectée à une facture du même client (§3.6)."""

    paiement = models.ForeignKey(
        Paiement, on_delete=models.PROTECT, related_name="affectations", verbose_name="paiement"
    )
    facture = models.ForeignKey(
        "facturation.Facture",
        on_delete=models.PROTECT,
        related_name="affectations",
        verbose_name="facture",
    )
    montant = models.DecimalField("montant affecté (FCFA)", **MONTANT)

    class Meta:
        verbose_name = "affectation de paiement"
        verbose_name_plural = "affectations de paiement"
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(condition=Q(montant__gt=0), name="affectation_montant_positif"),
        ]

    def __str__(self):
        return f"{self.montant} FCFA → {self.facture}"
