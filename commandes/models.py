from decimal import ROUND_HALF_UP, Decimal

import pghistory
from django.contrib.contenttypes.models import ContentType
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django_fsm import FSMField, FSMModelMixin, transition

from noyau.models import ModeleEntreprise, ModeleTrace, Validation


class Canal(models.TextChoices):
    WHATSAPP = "whatsapp", "WhatsApp"
    TELEPHONE = "telephone", "Téléphone"
    SUR_PLACE = "sur_place", "Sur place"
    COMMERCIAL = "commercial", "Commercial"
    BON_ECRIT = "bon_ecrit", "Bon de commande écrit"


class Statut(models.TextChoices):
    """Statuts du §8 du cahier des charges ; transitions du §4 de modele-donnees.md."""

    BROUILLON = "brouillon", "Brouillon"
    A_CONFIRMER = "a_confirmer", "À confirmer"
    CONFIRMEE = "confirmee", "Confirmée"
    A_PLANIFIER = "a_planifier", "À planifier"
    EN_PREPARATION = "en_preparation", "En préparation"
    PRETE_CHARGEMENT = "prete_chargement", "Prête au chargement"
    EN_LIVRAISON = "en_livraison", "En livraison"
    PARTIELLEMENT_LIVREE = "partiellement_livree", "Partiellement livrée"
    LIVREE = "livree", "Livrée"
    EN_ANOMALIE = "en_anomalie", "En anomalie"
    ANNULEE = "annulee", "Annulée"


# Annulation sans validation (motif seul) ; au-delà, validation d'un responsable (§4).
STATUTS_ANNULATION_SIMPLE = [Statut.BROUILLON, Statut.A_CONFIRMER]
STATUTS_ANNULATION_VALIDEE = [
    Statut.CONFIRMEE,
    Statut.A_PLANIFIER,
    Statut.EN_PREPARATION,
    Statut.PRETE_CHARGEMENT,
]
# Modification libre avant confirmation ; avec validation jusqu'à « prête au chargement ».
STATUTS_MODIFICATION_LIBRE = [Statut.BROUILLON, Statut.A_CONFIRMER]
STATUTS_MODIFICATION_VALIDEE = STATUTS_ANNULATION_VALIDEE


def arrondir_fcfa(montant):
    """Arrondi au franc le plus proche, la moitié vers le haut (décision 8.1)."""
    return Decimal(montant).quantize(Decimal(1), rounding=ROUND_HALF_UP)


class JourSemaine(models.IntegerChoices):
    LUNDI = 0, "lun"
    MARDI = 1, "mar"
    MERCREDI = 2, "mer"
    JEUDI = 3, "jeu"
    VENDREDI = 4, "ven"
    SAMEDI = 5, "sam"
    DIMANCHE = 6, "dim"


class ModeleRecurrent(ModeleEntreprise, ModeleTrace):
    """Commande type livrée certains jours ; génère chaque veille une commande à confirmer."""

    client = models.ForeignKey(
        "clients.Client", on_delete=models.PROTECT, related_name="modeles_recurrents"
    )
    adresse = models.ForeignKey("clients.Adresse", on_delete=models.PROTECT, related_name="+")
    jours_semaine = ArrayField(
        models.PositiveSmallIntegerField(choices=JourSemaine.choices),
        verbose_name="jours de livraison",
    )
    creneau = models.CharField("créneau", max_length=20)
    date_debut = models.DateField("début")
    date_fin = models.DateField("fin", null=True, blank=True)
    actif = models.BooleanField("actif", default=True)

    class Meta:
        verbose_name = "modèle récurrent"
        verbose_name_plural = "modèles récurrents"

    def __str__(self):
        jours = "–".join(JourSemaine(j).label for j in sorted(self.jours_semaine))
        return f"{self.client} {jours}"

    def clean(self):
        if self.adresse_id and self.client_id and self.adresse.client_id != self.client_id:
            raise ValidationError({"adresse": "Cette adresse n'appartient pas au client."})
        if self.date_fin and self.date_debut and self.date_fin < self.date_debut:
            raise ValidationError({"date_fin": "La fin précède le début."})


class LigneModeleRecurrent(ModeleEntreprise, ModeleTrace):
    modele = models.ForeignKey(ModeleRecurrent, on_delete=models.PROTECT, related_name="lignes")
    produit = models.ForeignKey("catalogue.Produit", on_delete=models.PROTECT, related_name="+")
    quantite = models.DecimalField(
        "quantité", max_digits=10, decimal_places=3, validators=[MinValueValidator(0)]
    )

    class Meta:
        verbose_name = "ligne de modèle récurrent"
        verbose_name_plural = "lignes de modèle récurrent"
        constraints = [
            models.CheckConstraint(
                condition=Q(quantite__gt=0), name="ligne_modele_quantite_positive"
            ),
        ]

    def clean(self):
        if self.produit_id and self.quantite is not None:
            if not self.produit.accepte_quantite(self.quantite):
                raise ValidationError({"quantite": _message_quantite(self.produit)})


def _message_quantite(produit):
    if produit.quantites_decimales:
        return "La quantité doit être positive."
    return f"La quantité doit être un nombre entier positif ({produit.get_unite_display()})."


@pghistory.track(model_name="HistoriqueCommande")
class Commande(FSMModelMixin, ModeleEntreprise, ModeleTrace):
    """Commande client. Le statut ne change que par les transitions ci-dessous,
    appelées par `commandes.services` (qui vérifie aussi les permissions)."""

    numero = models.CharField("numéro", max_length=20, editable=False)
    client = models.ForeignKey("clients.Client", on_delete=models.PROTECT, related_name="commandes")
    adresse = models.ForeignKey(
        "clients.Adresse", on_delete=models.PROTECT, related_name="+", verbose_name="adresse"
    )
    canal = models.CharField("canal de réception", max_length=20, choices=Canal.choices, blank=True)
    recue_le = models.DateTimeField("reçue le", default=timezone.now)
    date_livraison = models.DateField("date de livraison")
    creneau = models.CharField("créneau", max_length=20)
    urgente = models.BooleanField("urgente", default=False)
    instructions = models.TextField("instructions de livraison", blank=True)
    modele_recurrent = models.ForeignKey(
        ModeleRecurrent,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="commandes",
        verbose_name="modèle récurrent",
    )
    statut = FSMField(
        "statut", max_length=30, choices=Statut.choices, default=Statut.BROUILLON, protected=True
    )
    note_confirmation = models.TextField("note de confirmation", blank=True)
    motif_annulation = models.TextField("motif d'annulation", blank=True)

    class Meta:
        verbose_name = "commande"
        verbose_name_plural = "commandes"
        ordering = ["-date_livraison", "-numero"]
        constraints = [
            models.UniqueConstraint(
                fields=["entreprise", "numero"], name="commande_numero_unique_par_entreprise"
            ),
            models.UniqueConstraint(
                fields=["modele_recurrent", "date_livraison"],
                condition=Q(modele_recurrent__isnull=False),
                name="commande_une_par_modele_et_date",
            ),
            models.CheckConstraint(
                condition=Q(modele_recurrent__isnull=False) | ~Q(canal=""),
                name="commande_canal_obligatoire_hors_recurrente",
                violation_error_message="Indiquer le canal de réception.",
            ),
        ]

    def __str__(self):
        return self.numero

    def clean(self):
        if self.adresse_id and self.client_id and self.adresse.client_id != self.client_id:
            raise ValidationError({"adresse": "Cette adresse n'appartient pas au client."})

    def full_clean(self, exclude=None, **kwargs):
        # Le statut protégé ne peut pas être réécrit par la validation de Django :
        # il est contrôlé par les transitions.
        super().full_clean(exclude={*(exclude or ()), "statut"}, **kwargs)

    # --- Lecture ------------------------------------------------------------

    @property
    def lignes_actives(self):
        return self.lignes.filter(retiree=False)

    @property
    def montant_total(self):
        return sum((ligne.montant for ligne in self.lignes_actives), Decimal(0))

    @property
    def libelle_canal(self):
        return "Récurrente" if self.modele_recurrent_id else self.get_canal_display()

    def validation_acceptee(self, action):
        """Vrai si une `Validation` de cette action a été acceptée pour la commande."""
        return Validation.objects.filter(
            content_type=ContentType.objects.get_for_model(self),
            object_id=self.pk,
            action=action,
            decision=Validation.Decision.ACCEPTEE,
        ).exists()

    # --- Conditions des transitions ----------------------------------------

    def est_complete(self):
        """Client, adresse, date de livraison et au moins une ligne (§4, soumettre)."""
        return bool(
            self.client_id
            and self.adresse_id
            and self.date_livraison
            and self.creneau
            and self.lignes_actives.exists()
        )

    def urgence_validee(self):
        """Une commande urgente n'est confirmée qu'après validation de l'urgence (§4)."""
        return not self.urgente or self.validation_acceptee(Validation.Action.URGENCE)

    def annulation_autorisee(self):
        """Motif obligatoire ; validation d'annulation acceptée après confirmation (§4)."""
        if not self.motif_annulation.strip():
            return False
        if self.statut in STATUTS_ANNULATION_SIMPLE:
            return True
        return self.validation_acceptee(Validation.Action.ANNULATION)

    # --- Transitions (Sprint 2) --------------------------------------------

    @transition(
        field=statut,
        source=Statut.BROUILLON,
        target=Statut.A_CONFIRMER,
        conditions=[est_complete],
        permission=lambda commande, utilisateur: utilisateur.has_perm(
            "commandes.soumettre_commande", commande
        ),
    )
    def soumettre(self):
        """Saisie terminée : la commande attend la confirmation du client."""

    @transition(
        field=statut,
        source=Statut.A_CONFIRMER,
        target=Statut.CONFIRMEE,
        conditions=[est_complete, urgence_validee],
        permission=lambda commande, utilisateur: utilisateur.has_perm(
            "commandes.confirmer_commande", commande
        ),
    )
    def confirmer(self, note=""):
        """Confirmation du client enregistrée (auteur et heure dans l'historique)."""
        self.note_confirmation = note

    # --- Transitions (Sprint 3 : production) ---------------------------------

    def plan_valide(self):
        """Le plan de production de la date de livraison est validé (§4)."""
        from production.disponibilite import plan_valide

        return plan_valide(self)

    def prete_ou_exception(self):
        """Quantités disponibles pour tous ses produits, ou exception acceptée (§4, 8.2)."""
        from production.disponibilite import commande_couverte

        return commande_couverte(self) or self.validation_acceptee(Validation.Action.EXCEPTION)

    @transition(field=statut, source=Statut.CONFIRMEE, target=Statut.A_PLANIFIER)
    def inclure_au_plan(self):
        """Action du système : la commande est intégrée au plan de sa date de livraison."""

    @transition(
        field=statut,
        source=Statut.A_PLANIFIER,
        target=Statut.EN_PREPARATION,
        conditions=[plan_valide],
    )
    def lancer_preparation(self):
        """Action du système, à la validation du plan (ou plan déjà validé, décision 8.2)."""

    def plan_non_valide(self):
        return not self.plan_valide()

    @transition(
        field=statut,
        source=Statut.EN_PREPARATION,
        target=Statut.A_PLANIFIER,
        conditions=[plan_non_valide],
    )
    def replanifier(self):
        """Action du système : commande reportée vers un plan non validé (décision 8.2)."""

    @transition(
        field=statut,
        source=Statut.EN_PREPARATION,
        target=Statut.PRETE_CHARGEMENT,
        conditions=[prete_ou_exception],
        permission=lambda commande, utilisateur: utilisateur.has_perm(
            "commandes.preparer_commande", commande
        ),
    )
    def marquer_prete(self):
        """Quantités réservées : la commande peut être chargée (Sprint 4)."""

    # --- Transitions (Sprint 4 : chargement) ---------------------------------

    def chargee(self):
        """Chargement reçu par le livreur, ou départ autorisé par exception (§4, 8.3)."""
        from logistique.etat import commande_chargee

        return commande_chargee(self)

    @transition(
        field=statut,
        source=Statut.PRETE_CHARGEMENT,
        target=Statut.EN_LIVRAISON,
        conditions=[chargee],
    )
    def partir_en_livraison(self):
        """Action du système, au départ de la tournée (`logistique.services`)."""

    # --- Annulation -----------------------------------------------------------

    @transition(
        field=statut,
        source=[*STATUTS_ANNULATION_SIMPLE, *STATUTS_ANNULATION_VALIDEE],
        target=Statut.ANNULEE,
        conditions=[annulation_autorisee],
        permission=lambda commande, utilisateur: utilisateur.has_perm(
            "commandes.annuler_commande", commande
        ),
    )
    def annuler(self):
        """Annulation tracée (motif déjà renseigné). Aucune annulation après le départ."""


@pghistory.track(model_name="HistoriqueLigneCommande")
class LigneCommande(ModeleEntreprise, ModeleTrace):
    """Ligne de commande. Le prix unitaire est figé à la création (copie du catalogue).

    Une ligne enlevée est marquée `retiree` (aucune suppression physique).
    """

    commande = models.ForeignKey(Commande, on_delete=models.PROTECT, related_name="lignes")
    produit = models.ForeignKey(
        "catalogue.Produit", on_delete=models.PROTECT, related_name="+", verbose_name="produit"
    )
    quantite = models.DecimalField("quantité", max_digits=10, decimal_places=3)
    prix_unitaire = models.DecimalField(
        "prix unitaire (FCFA)", max_digits=14, decimal_places=0, editable=False
    )
    montant = models.DecimalField("montant (FCFA)", max_digits=14, decimal_places=0, editable=False)
    retiree = models.BooleanField("retirée", default=False)

    class Meta:
        verbose_name = "ligne de commande"
        verbose_name_plural = "lignes de commande"
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(condition=Q(quantite__gt=0), name="ligne_quantite_positive"),
            models.CheckConstraint(
                condition=Q(prix_unitaire__gte=0), name="ligne_prix_unitaire_positif"
            ),
        ]

    def __str__(self):
        return f"{self.produit} × {self.quantite.normalize()}"

    def clean(self):
        if self.produit_id and self.quantite is not None:
            if not self.produit.accepte_quantite(self.quantite):
                raise ValidationError({"quantite": _message_quantite(self.produit)})

    def save(self, *args, **kwargs):
        if self.prix_unitaire is None:
            self.prix_unitaire = self.produit.prix_reference
        self.montant = arrondir_fcfa(self.quantite * self.prix_unitaire)
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = {*kwargs["update_fields"], "montant"}
        super().save(*args, **kwargs)
