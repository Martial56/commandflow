"""Notifications planifiées (Celery Beat) : résumé du jour, paiements en retard, récurrentes
non confirmées à l'heure limite (§16.1 ; décisions 8.1 et 8.5).

Lancée toutes les 15 minutes : chaque notification a une clé par jour, elle n'est donc
envoyée qu'une fois par destinataire.
"""

import datetime

from celery import shared_task
from django.urls import reverse
from django.utils import timezone

from comptes.models import Role
from entreprises.models import Entreprise
from entreprises.parametres import heure_limite_confirmation, heure_resume
from noyau.contexte import activer_entreprise
from noyau.templatetags.format_fr import fcfa

from .models import TypeNotification
from .services import notifier


def _pluriel(nombre, singulier, pluriel=None):
    return f"{nombre} {singulier if nombre <= 1 else (pluriel or singulier + 's')}"


def resume_du_jour(date):
    """« Résumé du jour : 96 commandes à livrer (5 à confirmer), 3 en anomalie… »."""
    from commandes.models import Commande, Statut
    from facturation.alertes import documents_manquants
    from facturation.services import livraisons_a_facturer
    from paiements.alertes import factures_en_retard

    du_jour = Commande.objects.filter(date_livraison=date).exclude(
        statut__in=[Statut.BROUILLON, Statut.ANNULEE]
    )
    a_confirmer = du_jour.filter(statut=Statut.A_CONFIRMER).count()
    anomalies = Commande.objects.filter(statut=Statut.EN_ANOMALIE).count()
    retards = list(factures_en_retard())
    morceaux = [
        f"{_pluriel(du_jour.count(), 'commande')} à livrer ({a_confirmer} à confirmer)",
        f"{anomalies} en anomalie",
        f"{_pluriel(livraisons_a_facturer().count(), 'livraison')} à facturer",
        f"{_pluriel(documents_manquants().count(), 'document')} sans PDF",
        f"{_pluriel(len(retards), 'facture')} en retard "
        f"({fcfa(sum(f.solde for f in retards))} FCFA)",
    ]
    return "Résumé du jour : " + ", ".join(morceaux)


def _du_jour(date):
    notifier(
        TypeNotification.RESUME,
        resume_du_jour(date),
        roles=(Role.DIRECTION, Role.GESTIONNAIRE),
        lien=reverse("ecran_tableau_de_bord"),
        cle=f"resume-{date:%Y-%m-%d}",
        sauf_auteur=False,
    )
    from paiements.alertes import factures_en_retard

    retards = list(factures_en_retard())
    if retards:
        notifier(
            TypeNotification.PAIEMENT,
            f"{_pluriel(len(retards), 'facture')} en retard de paiement · "
            f"{fcfa(sum(f.solde for f in retards))} FCFA",
            roles=(Role.COMPTABILITE, Role.DIRECTION),
            lien=reverse("ecran_paiements"),
            cle=f"retards-{date:%Y-%m-%d}",
            sauf_auteur=False,
        )


def _recurrentes(date):
    """Récurrentes du lendemain encore à confirmer après l'heure limite (décision 8.1)."""
    from commandes.models import Commande, Statut

    demain = date + datetime.timedelta(days=1)
    nombre = Commande.objects.filter(
        date_livraison=demain, statut=Statut.A_CONFIRMER, modele_recurrent__isnull=False
    ).count()
    if nombre:
        notifier(
            TypeNotification.RECURRENTES,
            f"{_pluriel(nombre, 'commande récurrente', 'commandes récurrentes')} du "
            f"{demain:%d/%m} toujours à confirmer après l'heure limite",
            roles=(Role.COMMERCIAL, Role.GESTIONNAIRE),
            lien=reverse("ecran_recurrentes"),
            cle=f"recurrentes-{demain:%Y-%m-%d}",
            sauf_auteur=False,
        )


@shared_task
def notifications_planifiees(maintenant=None):
    """`maintenant` (ISO) sert aux tests."""
    maintenant = datetime.datetime.fromisoformat(maintenant) if maintenant else timezone.localtime()
    traitees = []
    for entreprise in Entreprise.objects.all():
        with activer_entreprise(entreprise):
            if maintenant.time() >= heure_resume(entreprise):
                _du_jour(maintenant.date())
            if maintenant.time() >= heure_limite_confirmation(entreprise):
                _recurrentes(maintenant.date())
        traitees.append(entreprise.slug)
    return traitees
