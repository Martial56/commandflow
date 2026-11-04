"""Facturation (§14 du cahier des charges ; §3.6 de modele-donnees.md, décisions 8.5).

Après la livraison, l'utilisateur habilité choisit « Facture FNE » ou « Autre document ».
ComandFlow archive le document (la FNE est émise sur la plateforme de la DGI) et suit son
paiement ; le choix ne change jamais le statut des livraisons. Toute écriture passe par
`facturation.services`.
"""

import pghistory
from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django_fsm import FSMField, FSMModelMixin, transition

from noyau.models import ModeleEntreprise, ModeleTrace

MONTANT = {"max_digits": 14, "decimal_places": 0}
LIEN_PORTAIL_FNE = "https://www.fne.dgi.gouv.ci/"


class TypeDocument(models.TextChoices):
    FNE = "fne", "Facture normalisée (FNE)"
    AUTRE = "autre", "Autre document (reçu, bon)"


class NatureAutreDocument(models.TextChoices):
    """Documents d'un traitement sans FNE (décision 8.5) ; jamais une imitation de FNE."""

    RECU = "recu", "Reçu"
    BON_LIVRAISON_VALORISE = "bon_livraison_valorise", "Bon de livraison valorisé"
    AUTRE = "autre", "Autre"


class StatutFacture(models.TextChoices):
    EMISE = "emise", "Émise"
    ANNULEE = "annulee", "Annulée"


@pghistory.track(model_name="HistoriqueFacture")
class Facture(FSMModelMixin, ModeleEntreprise, ModeleTrace):
    """Document de facturation d'un client, pour une ou plusieurs de ses livraisons (8.5).

    Les montants sont figés à l'émission. Le statut de paiement (non payé, partiel, payé,
    en retard) est calculé à partir des affectations, jamais saisi (§3.6).
    """

    numero_interne = models.CharField("numéro", max_length=20)
    client = models.ForeignKey(
        "clients.Client", on_delete=models.PROTECT, related_name="factures", verbose_name="client"
    )
    livraisons = models.ManyToManyField(
        "logistique.Livraison",
        through="FactureLivraison",
        related_name="factures",
        verbose_name="livraisons",
    )
    type_document = models.CharField(
        "type de document", max_length=10, choices=TypeDocument.choices
    )
    nature = models.CharField(
        "nature du document", max_length=30, choices=NatureAutreDocument.choices, blank=True
    )
    motif_sans_fne = models.CharField("motif (sans FNE)", max_length=255, blank=True)
    numero_fne = models.CharField("n° FNE (plateforme DGI)", max_length=60, blank=True)
    date_document = models.DateField("date du document")
    montant_ht = models.DecimalField("montant HT (FCFA)", **MONTANT)
    montant_ttc = models.DecimalField("montant TTC (FCFA)", **MONTANT)
    # PDF de la FNE ou du document, privé (lien signé). Peut être joint plus tard (8.5).
    piece = models.ForeignKey(
        "noyau.PieceJointe",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="document",
    )
    echeance = models.DateField("échéance de paiement")
    choix_fait_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="choix fait par",
    )
    choix_fait_le = models.DateTimeField("choix fait le")
    statut = FSMField(
        "statut",
        max_length=10,
        choices=StatutFacture.choices,
        default=StatutFacture.EMISE,
        protected=True,
    )
    motif_annulation = models.CharField("motif d'annulation", max_length=255, blank=True)
    # « Contesté / à vérifier » : marquage manuel de la Comptabilité (8.5).
    a_verifier = models.BooleanField("contesté / à vérifier", default=False)
    motif_verification = models.CharField("motif (à vérifier)", max_length=255, blank=True)

    class Meta:
        verbose_name = "facture"
        verbose_name_plural = "factures"
        ordering = ["-date_document", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["entreprise", "numero_interne"], name="facture_numero_unique"
            ),
            models.CheckConstraint(
                condition=Q(montant_ht__gte=0) & Q(montant_ht__lte=F("montant_ttc")),
                name="facture_montants_valides",
            ),
            # FNE : numéro obligatoire ; autre document : nature et motif (8.5).
            models.CheckConstraint(
                condition=(Q(type_document="fne") & ~Q(numero_fne=""))
                | (Q(type_document="autre") & ~Q(nature="") & ~Q(motif_sans_fne="")),
                name="facture_pieces_selon_le_type",
            ),
        ]

    def __str__(self):
        return self.numero_interne

    def full_clean(self, exclude=None, **kwargs):
        super().full_clean(exclude={*(exclude or ()), "statut"}, **kwargs)

    @property
    def est_fne(self):
        return self.type_document == TypeDocument.FNE

    @property
    def document_manquant(self):
        """FNE enregistrée sans son PDF : alerte « document manquant » (§16.1, 8.5)."""
        return self.statut == StatutFacture.EMISE and self.piece_id is None

    # --- Transitions ---

    def annulation_motivee(self):
        return bool(self.motif_annulation.strip())

    @transition(
        field=statut,
        source=StatutFacture.EMISE,
        target=StatutFacture.ANNULEE,
        conditions=[annulation_motivee],
        permission=lambda facture, utilisateur: utilisateur.has_perm(
            "facturation.annuler_facture", facture
        ),
    )
    def annuler(self):
        """Comptabilité ou Direction, avec motif ; les livraisons redeviennent à facturer.
        Pour une FNE, la correction sur le portail de la DGI reste à faire (8.5)."""


@pghistory.track(model_name="HistoriqueFactureLivraison")
class FactureLivraison(ModeleEntreprise, ModeleTrace):
    """Livraison couverte par un document, avec son montant figé à l'émission."""

    facture = models.ForeignKey(
        Facture, on_delete=models.PROTECT, related_name="lignes", verbose_name="facture"
    )
    livraison = models.ForeignKey(
        "logistique.Livraison",
        on_delete=models.PROTECT,
        related_name="lignes_facture",
        verbose_name="livraison",
    )
    montant_ht = models.DecimalField("montant HT (FCFA)", **MONTANT)
    montant_ttc = models.DecimalField("montant TTC (FCFA)", **MONTANT)

    class Meta:
        verbose_name = "livraison facturée"
        verbose_name_plural = "livraisons facturées"
        ordering = ["pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["facture", "livraison"], name="facture_livraison_unique"
            ),
            models.CheckConstraint(
                condition=Q(montant_ht__gte=0) & Q(montant_ht__lte=F("montant_ttc")),
                name="facture_livraison_montants_valides",
            ),
        ]

    def __str__(self):
        return f"{self.facture} · {self.livraison}"
