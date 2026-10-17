"""Fixtures partagées par toutes les apps."""

import pytest

from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory


@pytest.fixture
def entreprise(db):
    return EntrepriseFactory()


@pytest.fixture
def utilisateur(entreprise):
    return UtilisateurFactory(entreprise=entreprise)
