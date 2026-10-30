"""Fixtures communes des tests de la logistique."""

import pytest

from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory


@pytest.fixture
def personnes(entreprise):
    """Un utilisateur par rôle utile au chargement, dans l'entreprise du test."""

    def creer(role):
        return UtilisateurFactory(entreprise=entreprise, roles=[role])

    class Personnes:
        gestionnaire = creer(Role.GESTIONNAIRE)
        direction = creer(Role.DIRECTION)
        preparateur = creer(Role.PREPARATION)
        autre_chauffeur = creer(Role.CHAUFFEUR)

    return Personnes
