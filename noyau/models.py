from django.conf import settings
from django.db import models

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
