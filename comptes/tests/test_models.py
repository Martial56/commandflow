import pytest
from django.contrib.auth import authenticate, get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.test import RequestFactory

from comptes.models import Utilisateur
from comptes.tests.factories import UtilisateurFactory

pytestmark = pytest.mark.django_db


def test_modele_utilisateur_personnalise():
    assert get_user_model() is Utilisateur


def test_entreprise_protegee_par_ses_utilisateurs():
    utilisateur = UtilisateurFactory()
    with pytest.raises(ProtectedError):
        utilisateur.entreprise.delete()


def test_entreprise_obligatoire_pour_un_utilisateur_ordinaire():
    with pytest.raises(IntegrityError):
        UtilisateurFactory(entreprise=None)


def test_superutilisateur_plateforme_sans_entreprise():
    admin = Utilisateur.objects.create_superuser(
        username="plateforme", password="motdepasse-test", telephone="+2250100000000"
    )
    assert admin.entreprise is None


def test_telephone_unique():
    UtilisateurFactory(telephone="+2250700000001")
    with pytest.raises(IntegrityError):
        UtilisateurFactory(telephone="+2250700000001")


def test_telephone_format_international():
    utilisateur = UtilisateurFactory.build(telephone="07 00 00 00 01")
    with pytest.raises(ValidationError):
        utilisateur.full_clean()


def test_utilisateur_desactive_ne_peut_pas_se_connecter():
    utilisateur = UtilisateurFactory(username="awa")
    # django-axes exige la requête lors de l'authentification.
    requete = RequestFactory().post("/")
    identifiants = {"username": "awa", "password": "motdepasse-test"}
    assert authenticate(requete, **identifiants) == utilisateur
    utilisateur.is_active = False
    utilisateur.save()
    assert authenticate(requete, **identifiants) is None
