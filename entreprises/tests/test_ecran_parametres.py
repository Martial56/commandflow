"""Écran Paramètres (maquette 17, décision 8.6) : accès, comptes, preuves exigées, règles,
isolation entre entreprises."""

import datetime
import html
import re

import pytest
from django.urls import reverse

from comptes.models import Role, Utilisateur
from comptes.tests.factories import UtilisateurFactory
from entreprises.models import Entreprise, HistoriqueEntreprise
from entreprises.parametres import preuves_obligatoires, reglage_de_preuve
from entreprises.tests.factories import EntrepriseFactory

pytestmark = pytest.mark.django_db
R = Role
ECRAN = "ecran_parametres"


@pytest.fixture(autouse=True)
def sans_2fa_obligatoire(settings):
    settings.MIDDLEWARE = [
        m for m in settings.MIDDLEWARE if "DoubleAuthentificationObligatoire" not in m
    ]


@pytest.fixture
def entreprise():
    return EntrepriseFactory()


@pytest.fixture
def gestionnaire(client, entreprise):
    personne = UtilisateurFactory(entreprise=entreprise, roles=[R.GESTIONNAIRE])
    client.force_login(personne)
    return personne


def texte(reponse):
    contenu = html.unescape(reponse.content.decode())
    return " ".join(re.sub(r"<[^>]+>", " ", contenu).split())


@pytest.mark.parametrize(
    ("role", "attendu"),
    [(R.DIRECTION, 200), (R.GESTIONNAIRE, 200), (R.CHAUFFEUR, 403), (R.COMMERCIAL, 403)],
)
def test_acces_a_l_ecran(client, entreprise, role, attendu):
    client.force_login(UtilisateurFactory(entreprise=entreprise, roles=[role]))
    reponse = client.get(reverse(ECRAN))
    assert reponse.status_code == attendu
    if attendu == 200:
        assert "en préparation" not in texte(reponse)


def test_liste_des_comptes_de_l_entreprise_seulement(client, entreprise, gestionnaire):
    UtilisateurFactory(
        entreprise=entreprise, roles=[R.CHAUFFEUR], first_name="Konan", last_name="K."
    )
    UtilisateurFactory(
        entreprise=EntrepriseFactory(), roles=[R.CHAUFFEUR], first_name="Secret", last_name="B."
    )
    page = texte(client.get(reverse(ECRAN)))
    assert "Konan K." in page and "Chauffeur" in page and "jamais" in page
    assert "Secret" not in page
    # Le gestionnaire ne voit pas d'action sur son propre compte.
    assert page.count("Réinitialiser le mot de passe") == 1


def test_creation_affiche_le_mot_de_passe_une_seule_fois(client, entreprise, gestionnaire):
    reponse = client.post(
        reverse("parametres_ajouter_compte"),
        {"prenom": "Awa", "nom": "D.", "telephone": "+2250722334455", "role": R.PREPARATION},
    )
    page = texte(reponse)
    assert "Compte de Awa D. créé." in page
    provisoire = re.search(r"data-mot-de-passe>([^<]+)<", reponse.content.decode()).group(1)
    assert Utilisateur.objects.get(telephone="+2250722334455").check_password(provisoire)
    assert provisoire not in client.get(reverse(ECRAN)).content.decode()


def test_le_gestionnaire_ne_propose_pas_les_roles_sensibles(client, gestionnaire):
    contenu = client.get(reverse(ECRAN)).content.decode()
    options = re.findall(
        r'<option value="([a-z_]+)"', contenu.split('name="role"')[1].split("</select>")[0]
    )
    assert "chauffeur" in options and "direction" not in options and "comptabilite" not in options
    reponse = client.post(
        reverse("parametres_ajouter_compte"),
        {"prenom": "X", "nom": "Y", "telephone": "+2250799887766", "role": R.DIRECTION},
    )
    assert reponse.status_code == 400
    assert not Utilisateur.objects.filter(telephone="+2250799887766").exists()


def test_reinitialiser_et_desactiver_depuis_l_ecran(client, entreprise, gestionnaire):
    chauffeur = UtilisateurFactory(entreprise=entreprise, roles=[R.CHAUFFEUR])
    reponse = client.post(reverse("parametres_reinitialiser", args=[chauffeur.pk]))
    provisoire = re.search(r"data-mot-de-passe>([^<]+)<", reponse.content.decode()).group(1)
    assert Utilisateur.objects.get(pk=chauffeur.pk).check_password(provisoire)
    client.post(reverse("parametres_desactiver", args=[chauffeur.pk]))
    assert not Utilisateur.objects.get(pk=chauffeur.pk).is_active
    assert "Désactivé" in texte(client.get(reverse(ECRAN)))
    client.post(reverse("parametres_reactiver", args=[chauffeur.pk]))
    assert Utilisateur.objects.get(pk=chauffeur.pk).is_active


def test_actions_sur_un_compte_etranger_introuvables(client, gestionnaire):
    etranger = UtilisateurFactory(entreprise=EntrepriseFactory(), roles=[R.CHAUFFEUR])
    for nom in ("parametres_reinitialiser", "parametres_desactiver", "parametres_reactiver"):
        assert client.post(reverse(nom, args=[etranger.pk])).status_code == 404
    assert Utilisateur.objects.get(pk=etranger.pk).is_active


def test_regles_enregistrees(client, entreprise, gestionnaire):
    reponse = client.post(
        reverse("parametres_regles"),
        {
            "generation": "17:30",
            "limite": "19:45",
            "resume": "06:30",
            "creneaux": "05h–07h\n07h30–09h\n",
        },
    )
    assert reponse.url == reverse(ECRAN)
    entreprise.refresh_from_db()
    assert entreprise.options["heure_generation_recurrentes"] == "17:30"
    assert entreprise.options["heure_limite_confirmation"] == "19:45"
    assert entreprise.options["heure_resume"] == "06:30"
    assert entreprise.options["creneaux"] == ["05h–07h", "07h30–09h"]
    assert HistoriqueEntreprise.objects.filter(pgh_obj=entreprise).exists()


@pytest.mark.parametrize(
    ("donnees", "message"),
    [
        ({"limite": "17:00"}, "doit suivre l'heure de génération"),
        ({"creneaux": "Matin"}, "Créneau « Matin » : écrire par exemple 06h–08h."),
        ({"creneaux": "08h–06h"}, "Créneau « 08h–06h »"),
        ({"creneaux": "06h–08h\n06h–08h"}, "en double"),
    ],
)
def test_regles_refusees(client, entreprise, gestionnaire, donnees, message):
    valeurs = {"generation": "18:00", "limite": "20:00", "resume": "07:00", "creneaux": "06h–08h"}
    reponse = client.post(reverse("parametres_regles"), {**valeurs, **donnees})
    assert reponse.status_code == 400 and message in texte(reponse)
    assert Entreprise.objects.get(pk=entreprise.pk).options == {}


def grille(**cases):
    """Toutes les catégories en « photo ou réceptionnaire, un seul suffit », sauf `cases`."""
    from clients.models import Categorie

    donnees = {}
    for categorie in Categorie:
        donnees[f"preuve-{categorie.value}-photo_bordereau"] = "1"
        donnees[f"preuve-{categorie.value}-receptionnaire"] = "1"
        donnees[f"mode-{categorie.value}"] = "un"
    donnees.update(cases)
    return {cle: valeur for cle, valeur in donnees.items() if valeur is not None}


def test_preuves_enregistrees_et_appliquees(client, entreprise, gestionnaire):
    reponse = client.post(
        reverse("parametres_preuves"),
        grille(
            **{
                "preuve-restaurant-receptionnaire": None,
                "preuve-restaurant-signature": "1",
                "mode-restaurant": "tous",
            }
        ),
    )
    assert reponse.url == reverse(ECRAN)
    entreprise.refresh_from_db()
    assert preuves_obligatoires(entreprise, "restaurant") == [["photo_bordereau"], ["signature"]]
    assert preuves_obligatoires(entreprise, "particulier") == [
        ["photo_bordereau", "receptionnaire"]
    ]
    assert reglage_de_preuve(entreprise, "restaurant") == (["photo_bordereau", "signature"], "tous")


def test_preuves_par_defaut_affichees(client, gestionnaire):
    contenu = client.get(reverse(ECRAN)).content.decode()
    assert re.search(r'name="preuve-restaurant-photo_bordereau" value="1" checked', contenu)
    assert not re.search(r'name="preuve-restaurant-signature" value="1" checked', contenu)


def test_au_moins_un_moyen_par_categorie(client, entreprise, gestionnaire):
    client.post(
        reverse("parametres_preuves"),
        grille(
            **{
                "preuve-particulier-photo_bordereau": None,
                "preuve-particulier-receptionnaire": None,
            }
        ),
    )
    assert "preuves" not in (Entreprise.objects.get(pk=entreprise.pk).options or {})


def test_reglages_reserves_a_la_direction_et_au_gestionnaire(client, entreprise):
    from django.contrib.auth.models import Permission

    commercial = UtilisateurFactory(entreprise=entreprise, roles=[R.COMMERCIAL])
    commercial.user_permissions.add(Permission.objects.get(codename="gerer_utilisateurs"))
    client.force_login(commercial)
    assert client.get(reverse(ECRAN)).status_code == 200  # gestion des comptes seulement
    assert client.post(reverse("parametres_preuves"), grille()).status_code == 403
    valeurs = {"generation": "18:00", "limite": "20:00", "resume": "07:00", "creneaux": "06h–08h"}
    assert client.post(reverse("parametres_regles"), valeurs).status_code == 403


def test_derniere_connexion_lisible(client, entreprise, gestionnaire):
    from django.utils import timezone

    hier = timezone.now() - datetime.timedelta(days=1)
    UtilisateurFactory(entreprise=entreprise, roles=[R.CHAUFFEUR], last_login=hier)
    assert "hier " in texte(client.get(reverse(ECRAN)))
