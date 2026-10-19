"""Tâches planifiées des commandes (Celery Beat)."""

import datetime

from celery import shared_task
from django.utils import timezone

from entreprises.models import Entreprise
from entreprises.parametres import heure_generation_recurrentes
from noyau.contexte import activer_entreprise

from .services import generer_commandes_recurrentes


@shared_task
def generer_recurrentes(maintenant=None):
    """Génère les commandes récurrentes du lendemain pour chaque entreprise.

    Lancée toutes les 15 minutes : une entreprise est traitée dès que son heure de
    génération (18h00 par défaut, réglable) est passée. Les exécutions suivantes ne
    créent pas de doublon, et rattrapent un modèle créé après l'heure de génération.
    `maintenant` (ISO) sert aux tests.
    """
    maintenant = datetime.datetime.fromisoformat(maintenant) if maintenant else timezone.localtime()
    demain = maintenant.date() + datetime.timedelta(days=1)
    resultat = {}
    for entreprise in Entreprise.objects.all():
        if maintenant.time() < heure_generation_recurrentes(entreprise):
            continue
        with activer_entreprise(entreprise):
            rapport = generer_commandes_recurrentes(demain)
        resultat[entreprise.slug] = {
            "creees": [commande.numero for commande in rapport.creees],
            "ignorees": [f"{modele} : {raison}" for modele, raison in rapport.ignorees],
        }
    return resultat
