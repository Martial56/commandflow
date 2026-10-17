"""Entreprise et utilisateur courants, propres à chaque requête ou tâche.

Le middleware `entreprises.middleware.EntrepriseCouranteMiddleware` les définit pour
chaque requête. Hors requête (tâche Celery, commande de gestion, test), on les active
explicitement avec `activer_entreprise()` et `activer_utilisateur()`.
"""

from contextlib import contextmanager
from contextvars import ContextVar

_entreprise_courante = ContextVar("entreprise_courante", default=None)
_utilisateur_courant = ContextVar("utilisateur_courant", default=None)


class AucuneEntrepriseCourante(Exception):
    """Lecture de données métier sans entreprise courante : refusée par sécurité."""


class AutreEntreprise(Exception):
    """Écriture d'un objet rattaché à une autre entreprise que l'entreprise courante."""


def entreprise_courante():
    return _entreprise_courante.get()


def utilisateur_courant():
    return _utilisateur_courant.get()


@contextmanager
def activer_entreprise(entreprise):
    jeton = _entreprise_courante.set(entreprise)
    try:
        yield entreprise
    finally:
        _entreprise_courante.reset(jeton)


@contextmanager
def activer_utilisateur(utilisateur):
    jeton = _utilisateur_courant.set(utilisateur)
    try:
        yield utilisateur
    finally:
        _utilisateur_courant.reset(jeton)
