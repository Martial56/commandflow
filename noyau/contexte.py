"""Entreprise et utilisateur courants, propres à chaque requête ou tâche.

Le middleware `entreprises.middleware.EntrepriseCouranteMiddleware` les définit pour
chaque requête. Hors requête (tâche Celery, commande de gestion, test), on les active
explicitement avec `activer_entreprise()` et `activer_utilisateur()`.

Les deux sont aussi transmis à django-pghistory : chaque événement d'historique
enregistre l'utilisateur et l'entreprise à l'origine de la modification.
"""

from contextlib import contextmanager
from contextvars import ContextVar

import pghistory

_entreprise_courante = ContextVar("entreprise_courante", default=None)
_utilisateur_courant = ContextVar("utilisateur_courant", default=None)


class AucuneEntrepriseCourante(Exception):
    """Lecture de données métier sans entreprise courante : refusée par sécurité."""


class AutreEntreprise(Exception):
    """Écriture d'un objet rattaché à une autre entreprise que l'entreprise courante."""


class AucunUtilisateurCourant(Exception):
    """Action qui doit être attribuée à un utilisateur, lancée sans utilisateur courant."""


def entreprise_courante():
    return _entreprise_courante.get()


def utilisateur_courant():
    return _utilisateur_courant.get()


def entreprise_obligatoire():
    """Entreprise courante, ou `AucuneEntrepriseCourante` s'il n'y en a pas."""
    entreprise = _entreprise_courante.get()
    if entreprise is None:
        raise AucuneEntrepriseCourante("Action métier lancée sans entreprise courante.")
    return entreprise


@contextmanager
def activer_entreprise(entreprise):
    jeton = _entreprise_courante.set(entreprise)
    try:
        with _contexte_historique(entreprise=entreprise):
            yield entreprise
    finally:
        _entreprise_courante.reset(jeton)


@contextmanager
def activer_utilisateur(utilisateur):
    jeton = _utilisateur_courant.set(utilisateur)
    try:
        with _contexte_historique(utilisateur=utilisateur):
            yield utilisateur
    finally:
        _utilisateur_courant.reset(jeton)


@contextmanager
def _contexte_historique(**objets):
    """Place les clés primaires des objets dans le contexte django-pghistory.

    pghistory ne restaure pas les métadonnées à la sortie d'un contexte imbriqué :
    on le fait ici, pour qu'une modification faite après un changement temporaire
    d'utilisateur ou d'entreprise reste attribuée au bon auteur.
    """
    with pghistory.context() as contexte:
        anciennes = dict(contexte.metadata)
        contexte.metadata.update(
            {cle: objet.pk if objet is not None else None for cle, objet in objets.items()}
        )
        try:
            yield
        finally:
            contexte.metadata.clear()
            contexte.metadata.update(anciennes)
