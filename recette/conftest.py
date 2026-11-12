"""Recette dans un vrai navigateur (§22) : serveur de test, Chromium (Playwright) et
connexion réelle par le formulaire, code de vérification compris.

Chaque module de test porte `pytestmark = pytest.mark.recette` : la recette est exclue de
`pytest` et se lance par `pytest -m recette`.
"""

import time

import pytest
from allauth.mfa import app_settings as reglages_mfa
from allauth.mfa.totp.internal.auth import (
    TOTP,
    format_hotp_value,
    generate_totp_secret,
    hotp_value,
)
from django.contrib.auth.models import Group
from django.urls import reverse

from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory

# Mot de passe des utilisateurs créés par `UtilisateurFactory`.
MOT_DE_PASSE = "motdepasse-test"
# Profils à qui le code de vérification est imposé (comptes.middleware).
ROLES_AVEC_CODE = {Role.DIRECTION, Role.COMPTABILITE}
# Position simulée des téléphones : Plateau, Abidjan.
ABIDJAN = {"latitude": 5.3236, "longitude": -4.0197}
ECRAN_BUREAU = {"viewport": {"width": 1366, "height": 800}}


@pytest.fixture(scope="session", autouse=True)
def orm_pendant_le_navigateur():
    """Playwright (API synchrone) garde une boucle asyncio ouverte dans le fil des tests :
    sans ce réglage, Django refuserait les requêtes à la base (création de la base de test
    comprise). Limité à la session de recette, rétabli après."""
    with pytest.MonkeyPatch.context() as reglage:
        reglage.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
        yield


@pytest.fixture(autouse=True)
def groupes_des_roles(transactional_db):
    """Le serveur de test lit la base depuis un autre fil : les tests sont transactionnels
    et vident la base après eux, y compris les groupes créés par la migration
    `comptes.0002`. On les recrée."""
    for role in Role:
        Group.objects.get_or_create(name=role.label)


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    # /dev/shm est petit dans un conteneur Docker : Chromium utilise /tmp à la place.
    return {**browser_type_launch_args, "args": ["--disable-dev-shm-usage"]}


@pytest.fixture
def personne(entreprise):
    """Fabrique d'acteurs : `personne(Role.CHAUFFEUR, first_name="Yao")`, dans l'entreprise
    du test ou dans une autre (`dans=autre_entreprise`). Les profils Direction et
    Comptabilité reçoivent un code de vérification activé."""

    def creer(role, *, dans=None, **champs):
        utilisateur = UtilisateurFactory(entreprise=dans or entreprise, roles=[role], **champs)
        if role in ROLES_AVEC_CODE:
            utilisateur.secret_totp = generate_totp_secret()
            TOTP.activate(utilisateur, utilisateur.secret_totp)
        return utilisateur

    return creer


def code_de_verification(secret):
    """Code affiché à cet instant par l'application d'authentification.

    allauth n'accepte que le code de la période en cours (tolérance 0) : en fin de
    période, on attend le code suivant, comme le ferait l'utilisateur, pour qu'il soit
    encore valable quand le serveur le vérifie."""
    reste = reglages_mfa.TOTP_PERIOD - time.time() % reglages_mfa.TOTP_PERIOD
    if reste < 5:
        time.sleep(reste + 0.5)
    compteur = int(time.time()) // reglages_mfa.TOTP_PERIOD
    return format_hotp_value(hotp_value(secret, compteur))


def se_connecter(page, utilisateur):
    """Connexion par le formulaire, comme sur le terrain : téléphone, mot de passe, puis
    code de vérification si le profil l'exige."""
    page.goto(reverse("account_login"))
    page.get_by_label("Téléphone ou e-mail").fill(str(utilisateur.telephone))
    page.get_by_label("Mot de passe").fill(MOT_DE_PASSE)
    page.get_by_role("button", name="Se connecter").click()
    secret = getattr(utilisateur, "secret_totp", None)
    if secret:
        page.wait_for_url(f"**{reverse('mfa_authenticate')}")
        champ = page.locator("input[name=code]")
        champ.fill(code_de_verification(secret))
        champ.press("Enter")
    page.wait_for_url(lambda url: reverse("account_login") not in url)


@pytest.fixture
def ouvrir(browser, playwright, live_server):
    """`ouvrir(utilisateur)` : une session de navigateur connectée, au format bureau, ou
    au format téléphone avec `telephone=True` (Pixel 7, position GPS autorisée). Chaque
    appel ouvre une session distincte : plusieurs acteurs peuvent agir dans un même test."""
    contextes = []

    def ouvrir_session(utilisateur, telephone=False):
        appareil = playwright.devices["Pixel 7"] if telephone else ECRAN_BUREAU
        contexte = browser.new_context(
            **appareil,
            base_url=live_server.url,
            locale="fr-FR",
            timezone_id="Africa/Abidjan",
            geolocation=ABIDJAN,
            permissions=["geolocation"],
        )
        contextes.append(contexte)
        page = contexte.new_page()
        se_connecter(page, utilisateur)
        return page

    yield ouvrir_session
    for contexte in contextes:
        contexte.close()
