"""Notifications dans l'application (§16.1 ; décision 8.5 : ni e-mail, ni SMS, ni WhatsApp
au MVP). Une notification est adressée à une personne ; elle est lue, jamais supprimée.
Toute écriture passe par `notifications.services`.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q

from noyau.models import ModeleEntreprise, ModeleTrace


class TypeNotification(models.TextChoices):
    DEMANDE = "demande", "Demande à décider"
    PRODUCTION = "production", "Production"
    LIVRAISON = "livraison", "Livraison"
    RETOUR = "retour", "Retour"
    INCIDENT = "incident", "Incident"
    RECURRENTES = "recurrentes", "Récurrentes"
    PAIEMENT = "paiement", "Paiements"
    RESUME = "resume", "Résumé du jour"


class Notification(ModeleEntreprise, ModeleTrace):
    """Message pour un destinataire ; `cree_par` est la personne à l'origine de l'événement
    (vide pour une tâche planifiée)."""

    destinataire = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="notifications",
        verbose_name="destinataire",
    )
    type = models.CharField("type", max_length=20, choices=TypeNotification.choices)
    texte = models.CharField("texte", max_length=255)
    lien = models.CharField("lien", max_length=300, blank=True)
    lue_le = models.DateTimeField("lue le", null=True, blank=True)
    # Clé des notifications planifiées : une seule par destinataire (résumé du jour…).
    cle = models.CharField("clé", max_length=100, blank=True)

    class Meta:
        verbose_name = "notification"
        verbose_name_plural = "notifications"
        ordering = ["-pk"]
        indexes = [models.Index(fields=["destinataire", "lue_le"])]
        constraints = [
            models.UniqueConstraint(
                fields=["destinataire", "cle"],
                condition=~Q(cle=""),
                name="notification_cle_unique",
            ),
        ]

    def __str__(self):
        return f"{self.get_type_display()} · {self.texte}"
