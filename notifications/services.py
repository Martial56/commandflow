"""Création et lecture des notifications (§16.1 ; décision 8.5).

Les services des autres apps appellent `notifier` (ou `notifier_demande` pour une demande de
validation) dans leur transaction : si l'action échoue, la notification disparaît avec elle.
"""

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from comptes.models import Role
from noyau.contexte import entreprise_obligatoire, utilisateur_courant
from noyau.models import Validation

from .models import Notification, TypeNotification

RESPONSABLES = (Role.DIRECTION, Role.GESTIONNAIRE)
LIBELLES_DEMANDE = {
    Validation.Action.MODIFICATION: "Demande de modification",
    Validation.Action.ANNULATION: "Demande d'annulation",
    Validation.Action.URGENCE: "Commande urgente à valider",
    Validation.Action.SUBSTITUTION: "Demande de substitution",
    Validation.Action.ECART: "Écart à décider",
    Validation.Action.EXCEPTION: "Exception à décider",
}


def personnes(*roles):
    """Comptes actifs de l'entreprise courante ayant l'un de ces rôles."""
    return (
        get_user_model()
        .objects.filter(
            entreprise=entreprise_obligatoire(),
            is_active=True,
            groups__name__in=[Role(role).label for role in roles],
        )
        .distinct()
    )


def notifier(type, texte, *, roles=(), destinataires=(), lien="", cle="", sauf_auteur=True):
    """Notifie les personnes de ces rôles et ces destinataires, sauf l'auteur de l'action.

    `cle` : une seule notification par destinataire et par clé (tâches planifiées).
    """
    auteur = utilisateur_courant()
    cibles = {personne.pk: personne for personne in personnes(*roles)} if roles else {}
    cibles.update({personne.pk: personne for personne in destinataires})
    if sauf_auteur and auteur is not None:
        cibles.pop(auteur.pk, None)
    creees = []
    for destinataire in cibles.values():
        if cle and Notification.objects.filter(destinataire=destinataire, cle=cle).exists():
            continue
        try:
            with transaction.atomic():
                creees.append(
                    Notification.objects.create(
                        destinataire=destinataire,
                        type=type,
                        texte=texte[:255],
                        lien=lien,
                        cle=cle,
                    )
                )
        except IntegrityError:  # même clé créée au même instant par un autre processus
            continue
    return creees


def lien_objet(objet):
    """Écran où traiter l'objet d'une demande : fiche commande, ou tournées du jour."""
    from commandes.models import Commande
    from logistique.models import Chargement, Livraison

    if isinstance(objet, Commande):
        return reverse("commande_detail", args=[objet.pk])
    if isinstance(objet, Chargement | Livraison):
        tournee = objet.tournee
        return f"{reverse('ecran_tournees')}?date={tournee.date:%Y-%m-%d}"
    return ""


def _sujet(objet):
    from commandes.models import Commande

    if isinstance(objet, Commande):
        return f"{objet.numero} · {objet.client.nom}"
    commande = getattr(objet, "commande", None)
    return f"{commande.numero} · {commande.client.nom}" if commande else str(objet)


def notifier_demande(validation):
    """Une demande de validation prévient les responsables, sauf son auteur (8.1)."""
    objet = validation.objet
    libelle = LIBELLES_DEMANDE.get(validation.action, "Demande à décider")
    motif = f" : « {validation.motif} »" if validation.motif else ""
    return notifier(
        TypeNotification.DEMANDE,
        f"{libelle} · {_sujet(objet)}{motif}",
        roles=RESPONSABLES,
        lien=lien_objet(objet),
    )


# --- Lecture -------------------------------------------------------------------------


def non_lues(utilisateur):
    return Notification.objects.filter(destinataire=utilisateur, lue_le__isnull=True)


def marquer_lue(notification):
    if notification.lue_le is None:
        notification.lue_le = timezone.now()
        notification.save(update_fields=["lue_le", "modifie_le", "modifie_par"])
    return notification


def tout_marquer_lu(utilisateur):
    for notification in non_lues(utilisateur):
        marquer_lue(notification)
