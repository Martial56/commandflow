"""Application Celery : tâches de fond et tâches planifiées (Celery Beat).

Une tâche qui touche des données métier active l'entreprise concernée
(`noyau.contexte.activer_entreprise`) : hors requête, il n'y a pas d'entreprise courante.
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("comandflow")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
