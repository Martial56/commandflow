import pytest
from django.urls import reverse

from catalogue.models import Produit
from catalogue.tests.factories import ProduitFactory
from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory

pytestmark = pytest.mark.django_db

LISTE = reverse("admin:catalogue_produit_changelist")
AJOUT = reverse("admin:catalogue_produit_add")


@pytest.fixture(autouse=True)
def sans_2fa_obligatoire(settings):
    settings.MIDDLEWARE = [
        m for m in settings.MIDDLEWARE if "DoubleAuthentificationObligatoire" not in m
    ]


@pytest.mark.parametrize("role", [Role.DIRECTION, Role.GESTIONNAIRE])
def test_direction_et_gestionnaire_gerent_le_catalogue(client, role):
    client.force_login(UtilisateurFactory(roles=[role]))
    assert client.get(LISTE).status_code == 200
    assert client.get(AJOUT).status_code == 200


@pytest.mark.parametrize("role", [Role.COMMERCIAL, Role.CHAUFFEUR, Role.FACTURATION])
def test_autres_roles_refuses(client, role):
    client.force_login(UtilisateurFactory(roles=[role]))
    reponse = client.get(LISTE)
    assert reponse.status_code == 302
    assert reponse.url.startswith(reverse("admin:login"))


def test_liste_limitee_a_l_entreprise(client):
    gestionnaire = UtilisateurFactory(roles=[Role.GESTIONNAIRE])
    ProduitFactory(entreprise=gestionnaire.entreprise, nom="Pain de Mie Doré")
    ProduitFactory(entreprise=EntrepriseFactory(), nom="Pain d'ailleurs")
    client.force_login(gestionnaire)
    contenu = client.get(LISTE).content.decode()
    assert "Pain de Mie Doré" in contenu
    assert "ailleurs" not in contenu


def test_creation_rattachee_a_l_entreprise_et_tracee(client):
    gestionnaire = UtilisateurFactory(roles=[Role.GESTIONNAIRE])
    client.force_login(gestionnaire)
    reponse = client.post(
        AJOUT,
        {"code": "CRO", "nom": "Croissant", "unite": "piece", "prix_reference": "300"},
    )
    assert reponse.status_code == 302
    produit = Produit.toutes_entreprises.get(code="CRO")
    assert produit.entreprise == gestionnaire.entreprise
    assert produit.cree_par == gestionnaire


def test_suppression_impossible(client):
    gestionnaire = UtilisateurFactory(roles=[Role.GESTIONNAIRE])
    produit = ProduitFactory(entreprise=gestionnaire.entreprise)
    client.force_login(gestionnaire)
    url = reverse("admin:catalogue_produit_delete", args=[produit.pk])
    assert client.get(url).status_code == 403
    assert "delete_selected" not in client.get(LISTE).content.decode()


def test_action_desactiver(client):
    gestionnaire = UtilisateurFactory(roles=[Role.GESTIONNAIRE])
    produit = ProduitFactory(entreprise=gestionnaire.entreprise)
    client.force_login(gestionnaire)
    client.post(LISTE, {"action": "desactiver", "_selected_action": [produit.pk]})
    produit.refresh_from_db()
    assert produit.actif is False


def test_superutilisateur_plateforme_sans_catalogue(client):
    client.force_login(UtilisateurFactory(entreprise=None, is_superuser=True, is_staff=True))
    assert client.get(LISTE).status_code == 403
    assert "Catalogue" not in client.get(reverse("admin:index")).content.decode()
