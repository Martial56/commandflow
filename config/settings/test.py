"""Réglages des tests (pytest, CI)."""

from .dev import *  # noqa: F403
from .dev import INSTALLED_APPS

INSTALLED_APPS = [*INSTALLED_APPS, "noyau.tests"]

# Hachage rapide : les tests créent beaucoup d'utilisateurs.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Tâches Celery exécutées immédiatement, sans worker.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
