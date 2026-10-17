import pytest
from allauth.mfa.totp.internal.auth import TOTP, generate_totp_secret
from django.urls import reverse

from comptes.models import Role, Utilisateur
from comptes.tests.factories import UtilisateurFactory

pytestmark = pytest.mark.django_db

MOT_DE_PASSE = "motdepasse-test"
URL_CONNEXION = reverse("account_login")


def _se_connecter(client, identifiant, mot_de_passe=MOT_DE_PASSE):
    return client.post(URL_CONNEXION, {"login": identifiant, "password": mot_de_passe})


@pytest.fixture
def chauffeur():
    return UtilisateurFactory(
        telephone="+2250700000001", email="konan@exemple.ci", roles=[Role.CHAUFFEUR]
    )


# Connexion


def test_connexion_par_telephone(client, chauffeur):
    reponse = _se_connecter(client, "+2250700000001")
    assert reponse.status_code == 302
    assert reponse.url == reverse("accueil")
    assert client.get(reverse("accueil")).wsgi_request.user == chauffeur


def test_connexion_par_telephone_avec_espaces(client, chauffeur):
    assert _se_connecter(client, "+225 07 00 00 00 01").status_code == 302


def test_connexion_par_email(client, chauffeur):
    assert _se_connecter(client, "konan@exemple.ci").status_code == 302


def test_mauvais_mot_de_passe(client, chauffeur):
    reponse = _se_connecter(client, "+2250700000001", "faux")
    assert reponse.status_code == 200
    assert not reponse.wsgi_request.user.is_authenticated


def test_compte_desactive_refuse(client, chauffeur):
    chauffeur.is_active = False
    chauffeur.save()
    reponse = _se_connecter(client, "+2250700000001")
    assert not reponse.wsgi_request.user.is_authenticated


def test_accueil_exige_une_connexion(client):
    reponse = client.get(reverse("accueil"))
    assert reponse.status_code == 302
    assert reponse.url.startswith(URL_CONNEXION)


def test_inscription_publique_fermee(client):
    nombre = Utilisateur.objects.count()
    client.post(
        reverse("account_signup"),
        {"phone": "+2250799999999", "password1": "Xy7!secret-long", "password2": "Xy7!secret-long"},
    )
    assert Utilisateur.objects.count() == nombre


# Blocage après 5 échecs (django-axes)


def test_compte_bloque_apres_cinq_echecs(client, chauffeur):
    for _ in range(5):
        _se_connecter(client, "+2250700000001", "faux")
    reponse = _se_connecter(client, "+2250700000001")
    assert reponse.status_code == 429
    assert not reponse.wsgi_request.user.is_authenticated


def test_blocage_par_compte_et_non_par_poste(client, chauffeur):
    UtilisateurFactory(telephone="+2250700000002")
    for _ in range(5):
        _se_connecter(client, "+2250700000001", "faux")
    # Un collègue sur le même poste (même adresse IP) peut toujours se connecter.
    assert _se_connecter(client, "+2250700000002").status_code == 302


def test_quatre_echecs_puis_succes(client, chauffeur):
    for _ in range(4):
        _se_connecter(client, "+2250700000001", "faux")
    assert _se_connecter(client, "+2250700000001").status_code == 302


# Code de vérification obligatoire : Direction et Comptabilité


@pytest.mark.parametrize("role", [Role.DIRECTION, Role.COMPTABILITE])
def test_activation_2fa_imposee(client, role):
    UtilisateurFactory(telephone="+2250700000009", roles=[role])
    _se_connecter(client, "+2250700000009")

    # Toute page redirige vers l'activation du code de vérification.
    reponse = client.get(reverse("accueil"))
    assert reponse.status_code == 302
    assert reponse.url == reverse("mfa_activate_totp")

    # allauth demande de confirmer le mot de passe avant l'activation : page accessible.
    reponse = client.get(reverse("mfa_activate_totp"))
    assert reponse.url.startswith(reverse("account_reauthenticate"))
    assert client.get(reponse.url).status_code == 200

    # Après confirmation, la page d'activation s'affiche (pas de boucle de redirection).
    client.post(reverse("account_reauthenticate"), {"password": MOT_DE_PASSE})
    assert client.get(reverse("mfa_activate_totp")).status_code == 200


def test_2fa_non_imposee_au_chauffeur(client, chauffeur):
    client.force_login(chauffeur)
    assert client.get(reverse("accueil")).status_code == 200


def test_code_demande_a_la_connexion_quand_2fa_active(client):
    direction = UtilisateurFactory(telephone="+2250700000009", roles=[Role.DIRECTION])
    TOTP.activate(direction, generate_totp_secret())
    reponse = _se_connecter(client, "+2250700000009")
    assert reponse.status_code == 302
    assert reponse.url == reverse("mfa_authenticate")
    assert not client.get(reverse("accueil")).wsgi_request.user.is_authenticated


def test_acces_apres_activation_2fa(client):
    direction = UtilisateurFactory(roles=[Role.DIRECTION])
    TOTP.activate(direction, generate_totp_secret())
    client.force_login(direction)
    assert client.get(reverse("accueil")).status_code == 200
