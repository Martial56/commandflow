"""Socle de la recette : connexion réelle par profil, écran d'arrivée et JavaScript chargé
(htmx au bureau, Alpine et service worker sur le téléphone)."""

import re

import pytest
from django.urls import reverse
from playwright.sync_api import expect

from comptes.models import Role

pytestmark = pytest.mark.recette


def adresse(nom_url):
    return re.compile(re.escape(reverse(nom_url)) + "$")


def test_chauffeur_sur_telephone_arrive_sur_sa_tournee(ouvrir, personne):
    page = ouvrir(personne(Role.CHAUFFEUR, first_name="Yao"), telephone=True)
    expect(page).to_have_url(adresse("ecran_ma_tournee"))
    assert page.evaluate("typeof window.Alpine") == "object"
    assert page.evaluate("navigator.serviceWorker.ready.then(() => true)")


def test_gestionnaire_au_bureau_avec_htmx(ouvrir, personne):
    page = ouvrir(personne(Role.GESTIONNAIRE))
    assert page.evaluate("typeof window.htmx") == "object"
    expect(page.get_by_role("navigation").first).to_be_visible()


def test_direction_avec_code_de_verification(ouvrir, personne):
    page = ouvrir(personne(Role.DIRECTION))
    expect(page).to_have_url(adresse("ecran_tableau_de_bord"))
