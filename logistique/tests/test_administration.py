"""Véhicules et prestataires dans l'administration (décision 8.3)."""

import pytest
from django.urls import reverse

from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from logistique.models import Prestataire, Vehicule
from logistique.tests.factories import VehiculeFactory

pytestmark = pytest.mark.django_db

VEHICULES = reverse("admin:logistique_vehicule_changelist")
AJOUT_PRESTATAIRE = reverse("admin:logistique_prestataire_add")


@pytest.fixture(autouse=True)
def sans_2fa_obligatoire(settings):
    settings.MIDDLEWARE = [
        m for m in settings.MIDDLEWARE if "DoubleAuthentificationObligatoire" not in m
    ]


@pytest.mark.parametrize("role", [Role.DIRECTION, Role.GESTIONNAIRE])
def test_direction_et_gestionnaire(client, role):
    client.force_login(UtilisateurFactory(roles=[role]))
    assert client.get(VEHICULES).status_code == 200
    assert client.get(AJOUT_PRESTATAIRE).status_code == 200


@pytest.mark.parametrize("role", [Role.PREPARATION, Role.CHAUFFEUR, Role.COMMERCIAL])
def test_autres_roles_refuses(client, role):
    client.force_login(UtilisateurFactory(roles=[role]))
    assert client.get(VEHICULES).status_code == 302


def test_creation_d_un_vehicule_dans_l_entreprise(client):
    gestionnaire = UtilisateurFactory(roles=[Role.GESTIONNAIRE])
    client.force_login(gestionnaire)
    client.post(
        reverse("admin:logistique_vehicule_add"),
        {"immatriculation": "AB-0001-CI", "libelle": "Camionnette", "actif": "on"},
    )
    vehicule = Vehicule.toutes_entreprises.get()
    assert vehicule.entreprise == gestionnaire.entreprise and vehicule.cree_par == gestionnaire


def test_liste_limitee_a_l_entreprise(client):
    gestionnaire = UtilisateurFactory(roles=[Role.GESTIONNAIRE])
    VehiculeFactory(entreprise=gestionnaire.entreprise, immatriculation="ICI-0001")
    VehiculeFactory(entreprise=EntrepriseFactory(), immatriculation="AILLEURS-0002")
    client.force_login(gestionnaire)
    contenu = client.get(VEHICULES).content.decode()
    assert "ICI-0001" in contenu and "AILLEURS-0002" not in contenu


def test_compte_du_prestataire_limite_aux_prestataires_de_l_entreprise(client):
    gestionnaire = UtilisateurFactory(roles=[Role.GESTIONNAIRE])
    compte = UtilisateurFactory(entreprise=gestionnaire.entreprise, roles=[Role.PRESTATAIRE])
    chauffeur = UtilisateurFactory(entreprise=gestionnaire.entreprise, roles=[Role.CHAUFFEUR])
    etranger = UtilisateurFactory(roles=[Role.PRESTATAIRE])
    client.force_login(gestionnaire)
    contenu = client.get(AJOUT_PRESTATAIRE).content.decode()
    assert f'value="{compte.pk}"' in contenu
    assert f'value="{chauffeur.pk}"' not in contenu and f'value="{etranger.pk}"' not in contenu
    client.post(AJOUT_PRESTATAIRE, {"nom": "Moto Express", "compte": etranger.pk, "actif": "on"})
    assert not Prestataire.toutes_entreprises.exists()
    client.post(AJOUT_PRESTATAIRE, {"nom": "Moto Express", "compte": compte.pk, "actif": "on"})
    assert Prestataire.toutes_entreprises.get().compte == compte
