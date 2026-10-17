"""Fixtures partagées par toutes les apps."""

import pytest
from django.core.cache import cache

from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory


@pytest.fixture
def entreprise(db):
    return EntrepriseFactory()


@pytest.fixture
def utilisateur(entreprise):
    return UtilisateurFactory(entreprise=entreprise)


@pytest.fixture(autouse=True)
def cache_vide():
    """Les limites de débit d'allauth sont stockées en cache : on repart de zéro."""
    cache.clear()
