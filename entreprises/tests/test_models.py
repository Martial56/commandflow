import pytest
from django.db import IntegrityError

from entreprises.models import Entreprise

pytestmark = pytest.mark.django_db


def test_valeurs_par_defaut():
    entreprise = Entreprise.objects.create(nom="Mie Doré", slug="mie-dore")
    assert entreprise.devise == "XOF"
    assert entreprise.options == {}


def test_slug_unique():
    Entreprise.objects.create(nom="Mie Doré", slug="mie-dore")
    with pytest.raises(IntegrityError):
        Entreprise.objects.create(nom="Autre", slug="mie-dore")
