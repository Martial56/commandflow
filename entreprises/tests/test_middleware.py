import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

from entreprises.middleware import EntrepriseCouranteMiddleware
from noyau.contexte import entreprise_courante, utilisateur_courant

pytestmark = pytest.mark.django_db


def _executer(utilisateur):
    vu = {}

    def vue(request):
        vu["entreprise"] = entreprise_courante()
        vu["utilisateur"] = utilisateur_courant()
        return "réponse"

    requete = RequestFactory().get("/")
    requete.user = utilisateur
    reponse = EntrepriseCouranteMiddleware(vue)(requete)
    return reponse, vu


def test_utilisateur_connecte(utilisateur):
    reponse, vu = _executer(utilisateur)
    assert reponse == "réponse"
    assert vu["entreprise"] == utilisateur.entreprise
    assert vu["utilisateur"] == utilisateur
    # Contexte remis à zéro après la requête.
    assert entreprise_courante() is None
    assert utilisateur_courant() is None


def test_visiteur_anonyme():
    _, vu = _executer(AnonymousUser())
    assert vu["entreprise"] is None
    assert vu["utilisateur"] is None
