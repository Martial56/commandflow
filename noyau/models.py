import uuid
from pathlib import Path

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.files.storage import storages
from django.db import models
from django.utils import timezone
from django_fsm import FSMField, FSMModelMixin, transition

from .contexte import (
    AucuneEntrepriseCourante,
    AutreEntreprise,
    entreprise_courante,
    utilisateur_courant,
)


class GestionnaireEntreprise(models.Manager):
    """Ne renvoie que les données de l'entreprise courante ; refuse s'il n'y en a pas."""

    def get_queryset(self):
        entreprise = entreprise_courante()
        if entreprise is None:
            raise AucuneEntrepriseCourante(
                f"Lecture de {self.model._meta.label} sans entreprise courante. "
                "Utiliser activer_entreprise() ou le gestionnaire toutes_entreprises."
            )
        return super().get_queryset().filter(entreprise=entreprise)


class ModeleEntreprise(models.Model):
    """Base de tout modèle métier : rattachement à une entreprise et isolation des données.

    - `objects` : filtré sur l'entreprise courante (gestionnaire par défaut).
    - `toutes_entreprises` : sans filtre, réservé aux traitements système.
    """

    entreprise = models.ForeignKey(
        "entreprises.Entreprise",
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="entreprise",
    )

    # `objects` doit rester déclaré en premier : c'est lui le gestionnaire par défaut.
    objects = GestionnaireEntreprise()
    toutes_entreprises = models.Manager()  # noqa: DJ012

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        courante = entreprise_courante()
        if self.entreprise_id is None and courante is not None:
            self.entreprise = courante
        if courante is not None and self.entreprise_id != courante.pk:
            raise AutreEntreprise(
                f"Écriture de {self._meta.label} dans une autre entreprise que l'entreprise "
                "courante."
            )
        super().save(*args, **kwargs)


class ModeleTrace(models.Model):
    """Base de tout modèle métier : qui a créé et modifié l'objet, et quand.

    Les auteurs sont lus dans l'utilisateur courant. Un `QuerySet.update()` ne passe pas
    par `save()` et ne les met donc pas à jour.
    """

    cree_le = models.DateTimeField("créé le", auto_now_add=True)
    cree_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        editable=False,
        related_name="+",
        verbose_name="créé par",
    )
    modifie_le = models.DateTimeField("modifié le", auto_now=True)
    modifie_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        editable=False,
        related_name="+",
        verbose_name="modifié par",
    )

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        utilisateur = utilisateur_courant()
        if utilisateur is not None:
            if self._state.adding:
                self.cree_par = utilisateur
            self.modifie_par = utilisateur
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = {*kwargs["update_fields"], "modifie_par", "modifie_le"}
        super().save(*args, **kwargs)


class Compteur(models.Model):
    """Dernier numéro métier attribué, par entreprise, préfixe et année.

    `annee` est vide pour une numérotation continue (ex. CLI-000123).
    Toujours passer par `noyau.services.prochain_numero()`.
    """

    entreprise = models.ForeignKey(
        "entreprises.Entreprise",
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="entreprise",
    )
    prefixe = models.CharField("préfixe", max_length=10)
    annee = models.PositiveSmallIntegerField("année", null=True, blank=True)
    dernier_numero = models.PositiveIntegerField("dernier numéro", default=0)

    class Meta:
        verbose_name = "compteur"
        verbose_name_plural = "compteurs"
        constraints = [
            models.UniqueConstraint(
                fields=["entreprise", "prefixe", "annee"],
                nulls_distinct=False,
                name="compteur_unique_par_entreprise_prefixe_annee",
            ),
        ]

    def __str__(self):
        annee = f"-{self.annee}" if self.annee else ""
        return f"{self.prefixe}{annee} : {self.dernier_numero}"


class Validation(FSMModelMixin, ModeleEntreprise, ModeleTrace):
    """Décision d'un responsable sur un objet métier (§8, §10), tracée.

    La preuve éventuelle est une `PieceJointe` liée à la validation.
    Toujours passer par `noyau.services.demander_validation()` et `decider_validation()`.
    """

    class Action(models.TextChoices):
        MODIFICATION = "modification", "Modification"
        ANNULATION = "annulation", "Annulation"
        URGENCE = "urgence", "Urgence"
        SUBSTITUTION = "substitution", "Substitution"
        ECART = "ecart", "Écart"
        EXCEPTION = "exception", "Exception"

    class Decision(models.TextChoices):
        EN_ATTENTE = "en_attente", "En attente"
        ACCEPTEE = "acceptee", "Acceptée"
        REFUSEE = "refusee", "Refusée"

    content_type = models.ForeignKey(
        ContentType, on_delete=models.PROTECT, verbose_name="type d'objet"
    )
    object_id = models.PositiveBigIntegerField("identifiant de l'objet")
    objet = GenericForeignKey("content_type", "object_id")
    action = models.CharField("action", max_length=20, choices=Action.choices)
    motif = models.TextField("motif", blank=True)
    demandee_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="demandée par",
    )
    decision = FSMField(
        "décision",
        max_length=20,
        choices=Decision.choices,
        default=Decision.EN_ATTENTE,
        protected=True,
    )
    validee_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="décidée par",
    )
    decidee_le = models.DateTimeField("décidée le", null=True, blank=True)

    class Meta:
        verbose_name = "validation"
        verbose_name_plural = "validations"
        indexes = [models.Index(fields=["content_type", "object_id"])]

    def __str__(self):
        return f"{self.get_action_display()} — {self.get_decision_display()}"

    @transition(field=decision, source=Decision.EN_ATTENTE, target=Decision.ACCEPTEE)
    def accepter(self, par):
        self.validee_par = par
        self.decidee_le = timezone.now()

    @transition(field=decision, source=Decision.EN_ATTENTE, target=Decision.REFUSEE)
    def refuser(self, par):
        self.validee_par = par
        self.decidee_le = timezone.now()


def _stockage_prive():
    return storages["prive"]


def _chemin_piece_jointe(piece_jointe, nom_fichier):
    # Nom aléatoire : le nom d'origine (souvent parlant) n'apparaît pas dans le stockage.
    date = timezone.localdate()
    extension = Path(nom_fichier).suffix.lower()
    return f"{piece_jointe.entreprise_id}/{date:%Y/%m}/{uuid.uuid4().hex}{extension}"


class PieceJointe(ModeleEntreprise, ModeleTrace):
    """Fichier privé lié à un objet métier. Accès uniquement par lien signé."""

    class Type(models.TextChoices):
        BON_COMMANDE = "bon_commande", "Bon de commande"
        PHOTO = "photo", "Photo"
        JUSTIFICATIF = "justificatif", "Justificatif"

    content_type = models.ForeignKey(
        ContentType, on_delete=models.PROTECT, verbose_name="type d'objet"
    )
    object_id = models.PositiveBigIntegerField("identifiant de l'objet")
    objet = GenericForeignKey("content_type", "object_id")
    fichier = models.FileField("fichier", storage=_stockage_prive, upload_to=_chemin_piece_jointe)
    nom_original = models.CharField("nom d'origine", max_length=255)
    type = models.CharField("type", max_length=20, choices=Type.choices)

    class Meta:
        verbose_name = "pièce jointe"
        verbose_name_plural = "pièces jointes"
        indexes = [models.Index(fields=["content_type", "object_id"])]

    def __str__(self):
        return self.nom_original
