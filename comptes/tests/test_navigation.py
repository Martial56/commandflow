import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from comptes.models import Role
from comptes.navigation import ECRANS, ecran_arrivee, menus
from comptes.tests.factories import UtilisateurFactory

pytestmark = pytest.mark.django_db

R = Role
# Grille validée le 2026-10-18 (point A) : écrans autorisés par rôle.
GRILLE = {
    R.DIRECTION: {
        "tableau_de_bord", "commandes", "recurrentes", "clients", "production",
        "tournees", "facturation", "paiements", "parametres",
    },
    R.GESTIONNAIRE: {
        "tableau_de_bord", "commandes", "recurrentes", "clients", "production",
        "tournees", "facturation", "paiements", "parametres",
    },
    R.COMMERCIAL: {"tableau_de_bord", "commandes", "recurrentes", "clients"},
    R.PRODUCTION: {"production"},
    R.PREPARATION: {"chargement"},
    R.CHAUFFEUR: {"ma_tournee"},
    R.FACTURATION: {"commandes", "clients", "facturation"},
    R.COMPTABILITE: {"tableau_de_bord", "clients", "facturation", "paiements"},
    R.PRESTATAIRE: {"ma_tournee"},
}  # fmt: skip

ARRIVEE = {
    R.DIRECTION: "tableau_de_bord",
    R.GESTIONNAIRE: "tableau_de_bord",
    R.COMMERCIAL: "tableau_de_bord",
    R.PRODUCTION: "production",
    R.PREPARATION: "chargement",
    R.CHAUFFEUR: "ma_tournee",
    R.FACTURATION: "facturation",
    R.COMPTABILITE: "paiements",
    R.PRESTATAIRE: "ma_tournee",
}


@pytest.fixture
def client_2fa_ignoree(settings):
    """Navigation testée sans la redirection d'activation du code de vérification."""
    settings.MIDDLEWARE = [
        m for m in settings.MIDDLEWARE if "DoubleAuthentificationObligatoire" not in m
    ]


@pytest.mark.parametrize("role", list(GRILLE))
def test_grille_des_ecrans_par_role(role):
    utilisateur = UtilisateurFactory(roles=[role])
    autorises = {e.code for e in ECRANS if utilisateur.has_perm(e.permission)}
    assert autorises == GRILLE[role]


@pytest.mark.parametrize("role", list(GRILLE))
def test_acces_par_url_controle_cote_serveur(client, client_2fa_ignoree, role):
    client.force_login(UtilisateurFactory(roles=[role]))
    for ecran in ECRANS:
        attendu = 200 if ecran.code in GRILLE[role] else 403
        reponse = client.get(reverse(ecran.nom_url))
        assert reponse.status_code == attendu, (role, ecran.code)


@pytest.mark.parametrize("role", list(ARRIVEE))
def test_accueil_redirige_vers_l_ecran_du_role(client, client_2fa_ignoree, role):
    client.force_login(UtilisateurFactory(roles=[role]))
    reponse = client.get(reverse("accueil"))
    assert reponse.status_code == 302
    assert reponse.url == reverse(f"ecran_{ARRIVEE[role]}")


def test_barre_de_menus_limitee_aux_ecrans_du_role(client, client_2fa_ignoree):
    client.force_login(UtilisateurFactory(roles=[R.FACTURATION]))
    contenu = client.get(reverse("ecran_facturation")).content.decode()
    for libelle in ["Commandes", "Clients", "Facturation"]:
        assert f">{libelle}</a>" in contenu
    for libelle in ["Tableau de bord", "Récurrentes", "Production", "Tournées", "Paiements"]:
        assert f">{libelle}</a>" not in contenu
    assert 'aria-current="page">Facturation</a>' in contenu


def test_ecran_terrain_sans_barre_de_menus(client, client_2fa_ignoree):
    client.force_login(UtilisateurFactory(roles=[R.CHAUFFEUR]))
    contenu = client.get(reverse("ecran_ma_tournee")).content.decode()
    assert "Ma tournée" in contenu
    assert "Navigation principale" not in contenu


def test_plusieurs_roles_union_des_ecrans():
    utilisateur = UtilisateurFactory(roles=[R.COMMERCIAL, R.FACTURATION])
    assert {e.code for e in menus(utilisateur)} == {
        "tableau_de_bord", "commandes", "recurrentes", "clients", "facturation",
    }  # fmt: skip


def test_arrivee_selon_le_role_principal():
    utilisateur = UtilisateurFactory(roles=[R.FACTURATION, R.COMPTABILITE])
    assert utilisateur.role_principal == R.FACTURATION
    assert ecran_arrivee(utilisateur).code == "facturation"
    utilisateur.role_principal = R.COMPTABILITE
    assert ecran_arrivee(utilisateur).code == "paiements"


def test_gestion_des_utilisateurs_attribuee_individuellement(client, client_2fa_ignoree):
    chauffeur = UtilisateurFactory(roles=[R.CHAUFFEUR])
    assert not chauffeur.has_perm("ecrans.parametres")

    chauffeur.user_permissions.add(Permission.objects.get(codename="gerer_utilisateurs"))
    chauffeur = type(chauffeur).objects.get(pk=chauffeur.pk)  # vide le cache des permissions
    assert chauffeur.has_perm("ecrans.parametres")
    client.force_login(chauffeur)
    assert client.get(reverse("ecran_parametres")).status_code == 200


def test_aucun_role_aucun_ecran(client, client_2fa_ignoree):
    client.force_login(UtilisateurFactory())
    reponse = client.get(reverse("accueil"))
    assert reponse.status_code == 403
    assert "Aucun écran disponible" in reponse.content.decode()


def test_superutilisateur_plateforme_vers_l_administration(client):
    client.force_login(UtilisateurFactory(entreprise=None, is_superuser=True, is_staff=True))
    reponse = client.get(reverse("accueil"))
    assert reponse.url == reverse("admin:index")


def test_visiteur_anonyme_vers_la_connexion(client):
    reponse = client.get(reverse("ecran_commandes"))
    assert reponse.status_code == 302
    assert reponse.url.startswith(reverse("account_login"))


def test_page_de_connexion_conforme_a_la_maquette(client):
    contenu = client.get(reverse("account_login")).content.decode()
    for texte in [
        "Connexion",
        "Téléphone ou e-mail",
        "Mot de passe",
        "Se connecter",
        "Mot de passe oublié ?",
        "Après 5 échecs, le compte est bloqué 15 minutes.",
    ]:
        assert texte in contenu


def test_erreur_de_connexion_affichee(client):
    UtilisateurFactory(telephone="+2250700000001")
    reponse = client.post(reverse("account_login"), {"login": "+2250700000001", "password": "faux"})
    assert 'role="alert"' in reponse.content.decode()
