"""Chargement et tournées (§10, §11 du cahier des charges ; §3.5, §5 et §8.3 de modele-donnees.md).

Une tournée regroupe des chargements, un par commande. Chaque chargement est contrôlé
ligne par ligne par le préparateur, puis reçu par le chauffeur affecté lui-même.
Toute écriture passe par `logistique.services`.
"""

from decimal import Decimal

import pghistory
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django_fsm import FSMField, FSMModelMixin, transition

from noyau.models import ModeleEntreprise, ModeleTrace

QUANTITE = {"max_digits": 10, "decimal_places": 3}


def _permission(nom):
    """Permission django-rules d'une transition, évaluée sur l'objet."""
    return lambda objet, utilisateur: utilisateur.has_perm(nom, objet)


class _StatutProtege(FSMModelMixin):
    """Le statut (FSM protégé) est contrôlé par les transitions, pas par `full_clean`."""

    def full_clean(self, exclude=None, **kwargs):
        super().full_clean(exclude={*(exclude or ()), "statut"}, **kwargs)


# --- Référentiels --------------------------------------------------------------------


class Vehicule(ModeleEntreprise, ModeleTrace):
    """Véhicule de livraison (facultatif sur une tournée, décision 8.3)."""

    immatriculation = models.CharField("immatriculation", max_length=20)
    libelle = models.CharField("libellé", max_length=80, blank=True)
    actif = models.BooleanField("actif", default=True)

    class Meta:
        verbose_name = "véhicule"
        verbose_name_plural = "véhicules"
        constraints = [
            models.UniqueConstraint(
                fields=["entreprise", "immatriculation"], name="vehicule_immatriculation_unique"
            ),
        ]

    def __str__(self):
        return f"{self.libelle} {self.immatriculation}".strip()


class Prestataire(ModeleEntreprise, ModeleTrace):
    """Livreur externe ; avec un compte, il confirme lui-même la réception (8.3)."""

    nom = models.CharField("nom", max_length=120)
    telephone = models.CharField("téléphone", max_length=20, blank=True)
    compte = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="compte ComandFlow",
    )
    actif = models.BooleanField("actif", default=True)

    class Meta:
        verbose_name = "prestataire"
        verbose_name_plural = "prestataires"

    def __str__(self):
        return self.nom


# --- Tournée -------------------------------------------------------------------------


class StatutTournee(models.TextChoices):
    PLANIFIEE = "planifiee", "Planifiée"
    EN_CHARGEMENT = "en_chargement", "En chargement"
    DEPART_AUTORISE = "depart_autorise", "Départ autorisé"
    EN_COURS = "en_cours", "En cours"
    TERMINEE = "terminee", "Terminée"


@pghistory.track(model_name="HistoriqueTournee")
class Tournee(_StatutProtege, ModeleEntreprise, ModeleTrace):
    """Tournée d'une date, confiée à un chauffeur interne ou à un prestataire."""

    numero = models.CharField("numéro", max_length=20)
    date = models.DateField("date")
    chauffeur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="chauffeur",
    )
    prestataire = models.ForeignKey(
        Prestataire,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="tournees",
        verbose_name="prestataire",
    )
    vehicule = models.ForeignKey(
        Vehicule,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="véhicule",
    )
    statut = FSMField(
        "statut",
        max_length=20,
        choices=StatutTournee.choices,
        default=StatutTournee.PLANIFIEE,
        protected=True,
    )
    depart_le = models.DateTimeField("départ le", null=True, blank=True)
    fin_le = models.DateTimeField("fin le", null=True, blank=True)

    class Meta:
        verbose_name = "tournée"
        verbose_name_plural = "tournées"
        constraints = [
            models.UniqueConstraint(
                fields=["entreprise", "numero"], name="tournee_numero_unique_par_entreprise"
            ),
            # Un chauffeur ou un prestataire, pas les deux (§3.5).
            models.CheckConstraint(
                condition=Q(chauffeur__isnull=True) | Q(prestataire__isnull=True),
                name="tournee_chauffeur_ou_prestataire",
            ),
        ]

    def __str__(self):
        return self.numero

    @property
    def livreur(self):
        """Chauffeur interne ou prestataire, pour l'affichage."""
        return self.chauffeur or self.prestataire

    @property
    def compte_livreur(self):
        """Compte qui confirme la réception : le chauffeur, ou le compte du prestataire."""
        if self.chauffeur_id:
            return self.chauffeur
        return self.prestataire.compte if self.prestataire_id else None

    def chargements_actifs(self):
        return self.chargements.exclude(statut=StatutChargement.RETIRE)

    def tout_peut_partir(self):
        """Tous les chargements reçus, ou exception de départ acceptée (§5, 8.3)."""
        from .etat import peut_partir

        actifs = list(self.chargements_actifs())
        return bool(actifs) and all(peut_partir(chargement) for chargement in actifs)

    # --- Transitions (§5 « Statut de la tournée ») ---

    @transition(field="statut", source=StatutTournee.PLANIFIEE, target=StatutTournee.EN_CHARGEMENT)
    def commencer_chargement(self):
        """Action du système : premier contrôle commencé."""

    @transition(
        field="statut",
        source=StatutTournee.EN_CHARGEMENT,
        target=StatutTournee.DEPART_AUTORISE,
        conditions=[tout_peut_partir],
    )
    def autoriser_depart(self):
        """Action du système : chargements tous reçus ou couverts par une exception."""

    @transition(
        field="statut",
        source=StatutTournee.DEPART_AUTORISE,
        target=StatutTournee.EN_CHARGEMENT,
    )
    def rouvrir_chargement(self):
        """Action du système : commande ajoutée ou réception contestée avant le départ."""

    @transition(
        field="statut",
        source=StatutTournee.DEPART_AUTORISE,
        target=StatutTournee.EN_COURS,
        conditions=[tout_peut_partir],
        permission=_permission("logistique.recevoir_chargement"),
    )
    def demarrer(self):
        """Le livreur démarre la tournée : l'heure de départ est figée (§10)."""
        self.depart_le = timezone.now()

    @transition(field="statut", source=StatutTournee.EN_COURS, target=StatutTournee.TERMINEE)
    def terminer(self):
        """Action du système : toutes les livraisons traitées (Sprint 5)."""
        self.fin_le = timezone.now()


# --- Chargement ----------------------------------------------------------------------


class StatutChargement(models.TextChoices):
    A_PREPARER = "a_preparer", "À préparer"
    EN_VERIFICATION = "en_verification", "En vérification"
    ECART_CONSTATE = "ecart_constate", "Écart constaté"
    VERIFIEE_PREPARATION = "verifiee_preparation", "Vérifiée par la préparation"
    RECUE_CHAUFFEUR = "recue_chauffeur", "Reçue par le chauffeur"
    RETIRE = "retire", "Retiré de la tournée"  # décision 8.3


class MotifEcart(models.TextChoices):
    PRODUCTION_INSUFFISANTE = "production_insuffisante", "Production insuffisante"
    PRODUIT_ABIME = "produit_abime", "Produit abîmé"
    AUTRE = "autre", "Autre"


@pghistory.track(model_name="HistoriqueChargement")
class Chargement(_StatutProtege, ModeleEntreprise, ModeleTrace):
    """Une commande dans une tournée : contrôle du préparateur, réception du chauffeur."""

    tournee = models.ForeignKey(
        Tournee, on_delete=models.PROTECT, related_name="chargements", verbose_name="tournée"
    )
    commande = models.ForeignKey(
        "commandes.Commande",
        on_delete=models.PROTECT,
        related_name="chargements",
        verbose_name="commande",
    )
    ordre_passage = models.PositiveIntegerField("ordre de passage", default=1)
    statut = FSMField(
        "statut",
        max_length=25,
        choices=StatutChargement.choices,
        default=StatutChargement.A_PREPARER,
        protected=True,
    )
    verifie_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="vérifié par",
    )
    verifie_le = models.DateTimeField("vérifié le", null=True, blank=True)
    recu_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="reçu par",
    )
    recu_le = models.DateTimeField("reçu le", null=True, blank=True)
    motif_contestation = models.CharField("motif de contestation", max_length=255, blank=True)

    class Meta:
        verbose_name = "chargement"
        verbose_name_plural = "chargements"
        ordering = ["ordre_passage", "pk"]
        constraints = [
            # Unique par tournée et commande, sauf chargement retiré (décision 8.3).
            models.UniqueConstraint(
                fields=["tournee", "commande"],
                condition=~Q(statut="retire"),
                name="chargement_unique_par_tournee_et_commande",
            ),
        ]

    def __str__(self):
        return f"{self.tournee} · arrêt {self.ordre_passage} · {self.commande}"

    # --- Conditions (§5) ---

    def tout_conforme(self):
        """Chaque ligne comptée et conforme à l'attendu."""
        lignes = list(self.lignes.all())
        return bool(lignes) and all(ligne.conforme for ligne in lignes)

    def ecart_motive(self):
        """Toutes les lignes comptées, au moins un écart, chaque écart avec son motif."""
        lignes = list(self.lignes.all())
        if not lignes or any(ligne.quantite_preparee is None for ligne in lignes):
            return False
        en_ecart = [ligne for ligne in lignes if not ligne.conforme]
        return bool(en_ecart) and all(ligne.motif_ecart for ligne in en_ecart)

    def exception_ecart_acceptee(self):
        from .etat import exception_acceptee

        return exception_acceptee(self, "ecart")

    def quantites_retenues(self):
        from .etat import quantites_retenues

        return quantites_retenues(self)

    def reception_conforme(self):
        """Quantité reçue saisie sur chaque ligne, égale à la quantité retenue
        (le préparé, ou la quantité d'une exception d'écart acceptée)."""
        lignes = list(self.lignes.all())
        retenues = self.quantites_retenues()
        return bool(lignes) and all(
            ligne.quantite_recue_chauffeur is not None
            and ligne.quantite_recue_chauffeur == retenues[ligne.pk]
            for ligne in lignes
        )

    def reception_contestee(self):
        """Motif saisi et au moins une ligne reçue différente de la quantité retenue."""
        retenues = self.quantites_retenues()
        return bool(self.motif_contestation.strip()) and any(
            ligne.quantite_recue_chauffeur is not None
            and ligne.quantite_recue_chauffeur != retenues[ligne.pk]
            for ligne in self.lignes.all()
        )

    def tournee_pas_partie(self):
        return self.tournee.statut not in (StatutTournee.EN_COURS, StatutTournee.TERMINEE)

    # --- Transitions (§5 « Statut du chargement ») ---

    @transition(
        field="statut",
        source=StatutChargement.A_PREPARER,
        target=StatutChargement.EN_VERIFICATION,
        conditions=[tournee_pas_partie],
        permission=_permission("logistique.controler_chargement"),
    )
    def commencer_verification(self):
        """Le préparateur commence le contrôle physique."""

    @transition(
        field="statut",
        source=StatutChargement.EN_VERIFICATION,
        target=StatutChargement.VERIFIEE_PREPARATION,
        conditions=[tournee_pas_partie, tout_conforme],
        permission=_permission("logistique.controler_chargement"),
    )
    def valider_preparation(self, par):
        """Toutes les lignes comptées et conformes."""
        self.verifie_par = par
        self.verifie_le = timezone.now()

    @transition(
        field="statut",
        source=StatutChargement.EN_VERIFICATION,
        target=StatutChargement.ECART_CONSTATE,
        conditions=[tournee_pas_partie, ecart_motive],
        permission=_permission("logistique.controler_chargement"),
    )
    def constater_ecart(self, par):
        """Quantité différente, avec son motif : une décision est requise (§10)."""
        self.verifie_par = par
        self.verifie_le = timezone.now()

    @transition(
        field="statut",
        source=StatutChargement.ECART_CONSTATE,
        target=StatutChargement.EN_VERIFICATION,
        conditions=[tournee_pas_partie],
        permission=_permission("logistique.controler_chargement"),
    )
    def completer(self):
        """Marchandise complétée : nouveau contrôle."""

    @transition(
        field="statut",
        source=StatutChargement.ECART_CONSTATE,
        target=StatutChargement.VERIFIEE_PREPARATION,
        conditions=[tournee_pas_partie, exception_ecart_acceptee],
    )
    def accepter_exception(self):
        """Exception validée par un responsable : les quantités chargées font foi (8.3)."""

    @transition(
        field="statut",
        source=StatutChargement.VERIFIEE_PREPARATION,
        target=StatutChargement.RECUE_CHAUFFEUR,
        conditions=[tournee_pas_partie, reception_conforme],
        permission=_permission("logistique.recevoir_chargement"),
    )
    def confirmer_reception(self, par):
        """Le livreur affecté confirme lui-même ; jamais le préparateur (§10)."""
        self.recu_par = par
        self.recu_le = timezone.now()

    @transition(
        field="statut",
        source=StatutChargement.VERIFIEE_PREPARATION,
        target=StatutChargement.ECART_CONSTATE,
        conditions=[tournee_pas_partie, reception_contestee],
        permission=_permission("logistique.recevoir_chargement"),
    )
    def contester(self):
        """Le livreur conteste une quantité : l'écart est consigné avant le départ (§10)."""

    @transition(
        field="statut",
        source=[
            StatutChargement.A_PREPARER,
            StatutChargement.EN_VERIFICATION,
            StatutChargement.ECART_CONSTATE,
            StatutChargement.VERIFIEE_PREPARATION,
            StatutChargement.RECUE_CHAUFFEUR,
        ],
        target=StatutChargement.RETIRE,
        conditions=[tournee_pas_partie],
        permission=_permission("logistique.gerer_tournee"),
    )
    def retirer(self):
        """Commande retirée de la tournée avant le départ ; elle redevient affectable (8.3)."""


@pghistory.track(model_name="HistoriqueLigneChargement")
class LigneChargement(ModeleEntreprise, ModeleTrace):
    """Produit d'un chargement : attendu, préparé (préparateur), reçu (chauffeur)."""

    chargement = models.ForeignKey(
        Chargement, on_delete=models.PROTECT, related_name="lignes", verbose_name="chargement"
    )
    ligne_commande = models.ForeignKey(
        "commandes.LigneCommande",
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="ligne de commande",
    )
    quantite_attendue = models.DecimalField("attendu", **QUANTITE)
    quantite_preparee = models.DecimalField("préparé", null=True, blank=True, **QUANTITE)
    quantite_recue_chauffeur = models.DecimalField(
        "reçu par le chauffeur", null=True, blank=True, **QUANTITE
    )
    motif_ecart = models.CharField(
        "motif de l'écart", max_length=30, choices=MotifEcart.choices, blank=True
    )
    detail_ecart = models.CharField("précision", max_length=255, blank=True)

    class Meta:
        verbose_name = "ligne de chargement"
        verbose_name_plural = "lignes de chargement"
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(
                condition=Q(quantite_attendue__gte=0)
                & (Q(quantite_preparee__isnull=True) | Q(quantite_preparee__gte=0))
                & (Q(quantite_recue_chauffeur__isnull=True) | Q(quantite_recue_chauffeur__gte=0)),
                name="ligne_chargement_quantites_positives",
            ),
        ]

    def __str__(self):
        return f"{self.ligne_commande.produit} · {self.quantite_attendue}"

    @property
    def ecart(self):
        """Préparé − attendu (négatif : manquant) ; nul tant que rien n'est compté."""
        if self.quantite_preparee is None:
            return None
        return self.quantite_preparee - self.quantite_attendue

    @property
    def conforme(self):
        return self.ecart == Decimal("0")


# --- Livraison (créée au départ ; complétée au Sprint 5) -------------------------------


class StatutLivraison(models.TextChoices):
    EN_ATTENTE = "en_attente", "En attente"
    SUR_PLACE = "sur_place", "Sur place"
    LIVREE = "livree", "Livrée"
    PARTIELLEMENT_LIVREE = "partiellement_livree", "Partiellement livrée"
    ECHOUEE = "echouee", "Échouée"
    REPORTEE = "reportee", "Reportée"


@pghistory.track(model_name="HistoriqueLivraison")
class Livraison(_StatutProtege, ModeleEntreprise, ModeleTrace):
    """Livraison d'un chargement, créée « en attente » au départ de la tournée (§7)."""

    chargement = models.OneToOneField(
        Chargement, on_delete=models.PROTECT, related_name="livraison", verbose_name="chargement"
    )
    commande = models.ForeignKey(
        "commandes.Commande",
        on_delete=models.PROTECT,
        related_name="livraisons",
        verbose_name="commande",
    )
    statut = FSMField(
        "statut",
        max_length=25,
        choices=StatutLivraison.choices,
        default=StatutLivraison.EN_ATTENTE,
        protected=True,
    )

    class Meta:
        verbose_name = "livraison"
        verbose_name_plural = "livraisons"

    def __str__(self):
        return f"Livraison {self.commande}"
