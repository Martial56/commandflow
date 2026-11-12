"""Gestion des comptes par les responsables (écran Paramètres, décision 8.6) : création avec
mot de passe provisoire, réinitialisation (B1), désactivation, droits et historique."""

import pytest
from allauth.mfa.models import Authenticator
from allauth.mfa.totp.internal.auth import TOTP, generate_totp_secret
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.urls import reverse

from comptes import services
from comptes.models import HistoriqueUtilisateur, Role, Utilisateur
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from noyau.contexte import activer_entreprise, activer_utilisateur

pytestmark = pytest.mark.django_db
R = Role


@pytest.fixture
def entreprise():
    entreprise = EntrepriseFactory()
    with activer_entreprise(entreprise):
        yield entreprise


@pytest.fixture
def gens(entreprise):
    def creer(role):
        return UtilisateurFactory(entreprise=entreprise, roles=[role])

    class Gens:
        direction = creer(R.DIRECTION)
        gestionnaire = creer(R.GESTIONNAIRE)
        comptable = creer(R.COMPTABILITE)
        chauffeur = creer(R.CHAUFFEUR)

    return Gens


def creer(par, **champs):
    donnees = {"prenom": "Yao", "nom": "S.", "telephone": "+225 07 11 22 33 44"}
    donnees.update(champs)
    donnees.setdefault("role", R.CHAUFFEUR)
    with activer_utilisateur(par):
        return services.creer_compte(**donnees)


# --- Création ---------------------------------------------------------------------------


def test_creation_par_le_gestionnaire(entreprise, gens):
    compte, provisoire = creer(gens.gestionnaire)
    compte = Utilisateur.objects.get(pk=compte.pk)
    assert (compte.telephone, compte.username) == ("+2250711223344", "+2250711223344")
    assert compte.entreprise == entreprise and compte.role_principal == R.CHAUFFEUR
    assert compte.groups.filter(name="Chauffeur").exists()
    assert compte.check_password(provisoire) and compte.doit_changer_mot_de_passe
    assert len(provisoire) == 14 and provisoire.count("-") == 2


@pytest.mark.parametrize("role", [R.DIRECTION, R.COMPTABILITE])
def test_comptes_sensibles_crees_par_la_direction_seule(gens, role):
    with pytest.raises(services.ActionImpossible, match="Seule la Direction"):
        creer(gens.gestionnaire, role=role)
    compte, _ = creer(gens.direction, role=role)
    assert compte.role_principal == role


@pytest.mark.parametrize(
    ("champs", "message"),
    [
        ({"telephone": "0711223344"}, "format international"),
        ({"prenom": " "}, "prénom et le nom"),
        ({"role": R.PRESTATAIRE}, "Choisir un rôle"),
    ],
)
def test_creation_refusee(gens, champs, message):
    with pytest.raises(services.ActionImpossible, match=message):
        creer(gens.gestionnaire, **champs)
    assert not Utilisateur.objects.filter(first_name="Yao").exists()


def test_telephone_deja_utilise(gens):
    creer(gens.gestionnaire)
    with pytest.raises(services.ActionImpossible, match="déjà un compte"):
        creer(gens.gestionnaire, prenom="Autre")


def test_creation_interdite_sans_droit(gens):
    with pytest.raises(PermissionDenied):
        creer(gens.chauffeur)


def test_permission_individuelle_suffit(entreprise):
    commercial = UtilisateurFactory(entreprise=entreprise, roles=[R.COMMERCIAL])
    commercial.user_permissions.add(Permission.objects.get(codename="gerer_utilisateurs"))
    commercial = Utilisateur.objects.get(pk=commercial.pk)
    compte, _ = creer(commercial)
    assert compte.pk


# --- Réinitialisation (B1) --------------------------------------------------------------


def reinitialiser(par, cible):
    with activer_utilisateur(par):
        return services.reinitialiser_mot_de_passe(cible)


def test_reinitialisation_par_le_gestionnaire(gens):
    provisoire = reinitialiser(gens.gestionnaire, gens.chauffeur)
    chauffeur = Utilisateur.objects.get(pk=gens.chauffeur.pk)
    assert chauffeur.check_password(provisoire) and chauffeur.doit_changer_mot_de_passe


def test_reinitialisation_leve_le_blocage(client, gens):
    for _ in range(5):
        client.post(
            reverse("account_login"), {"login": gens.chauffeur.telephone, "password": "faux"}
        )
    provisoire = reinitialiser(gens.gestionnaire, gens.chauffeur)
    reponse = client.post(
        reverse("account_login"), {"login": gens.chauffeur.telephone, "password": provisoire}
    )
    assert reponse.status_code == 302


def test_la_direction_efface_le_code_de_verification(gens):
    TOTP.activate(gens.comptable, generate_totp_secret())
    reinitialiser(gens.direction, gens.comptable)
    assert not Authenticator.objects.filter(user=gens.comptable).exists()


@pytest.mark.parametrize("cible", ["direction", "comptable"])
def test_le_gestionnaire_ne_touche_pas_aux_comptes_sensibles(gens, cible):
    with pytest.raises(PermissionDenied):
        reinitialiser(gens.gestionnaire, getattr(gens, cible))
    with pytest.raises(PermissionDenied), activer_utilisateur(gens.gestionnaire):
        services.desactiver_compte(getattr(gens, cible))


def test_personne_ne_gere_son_propre_compte(gens):
    with pytest.raises(PermissionDenied):
        reinitialiser(gens.direction, gens.direction)
    with pytest.raises(PermissionDenied), activer_utilisateur(gens.direction):
        services.desactiver_compte(gens.direction)


def test_compte_d_une_autre_entreprise_inaccessible(gens):
    with activer_entreprise(None):
        etranger = UtilisateurFactory(entreprise=EntrepriseFactory(), roles=[R.CHAUFFEUR])
    with pytest.raises(PermissionDenied):
        reinitialiser(gens.direction, etranger)


# --- Désactivation et historique --------------------------------------------------------


def test_desactivation_puis_reactivation(client, gens):
    with activer_utilisateur(gens.gestionnaire):
        services.desactiver_compte(gens.chauffeur)
        with pytest.raises(services.ActionImpossible, match="déjà désactivé"):
            services.desactiver_compte(gens.chauffeur)
    chauffeur = Utilisateur.objects.get(pk=gens.chauffeur.pk)
    assert not chauffeur.is_active
    reponse = client.post(
        reverse("account_login"),
        {"login": chauffeur.telephone, "password": "motdepasse-test"},
    )
    assert reponse.status_code != 302 or reponse.url == reverse("account_inactive")
    with activer_utilisateur(gens.gestionnaire):
        services.reactiver_compte(chauffeur)
    assert Utilisateur.objects.get(pk=chauffeur.pk).is_active


def test_historique_sans_mot_de_passe(gens):
    compte, _ = creer(gens.gestionnaire)
    with activer_utilisateur(gens.gestionnaire):
        services.desactiver_compte(compte)
    champs = {champ.name for champ in HistoriqueUtilisateur._meta.get_fields()}
    assert "password" not in champs and "last_login" not in champs
    etats = list(
        HistoriqueUtilisateur.objects.filter(pgh_obj=compte)
        .order_by("pgh_id")
        .values_list("is_active", flat=True)
    )
    assert etats[0] is True and etats[-1] is False


# --- Mot de passe provisoire à changer à la connexion -----------------------------------


def test_mot_de_passe_provisoire_a_changer(client, gens):
    compte, provisoire = creer(gens.gestionnaire)
    client.post(reverse("account_login"), {"login": compte.telephone, "password": provisoire})
    changement = reverse("account_change_password")
    for page in (reverse("accueil"), reverse("ecran_ma_tournee")):
        reponse = client.get(page)
        assert (reponse.status_code, reponse.url) == (302, changement)
    assert client.get(changement).status_code == 200
    reponse = client.post(
        changement,
        {"oldpassword": provisoire, "password1": "Baguette-2026!", "password2": "Baguette-2026!"},
    )
    assert reponse.url == reverse("accueil")
    compte = Utilisateur.objects.get(pk=compte.pk)
    assert not compte.doit_changer_mot_de_passe and compte.check_password("Baguette-2026!")
    assert client.get(reverse("ecran_ma_tournee")).status_code == 200
