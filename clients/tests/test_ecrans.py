import pytest
from django.urls import reverse

from clients.models import Client
from clients.tests.factories import AdresseFactory, ClientFactory
from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory

pytestmark = pytest.mark.django_db

LISTE = reverse("ecran_clients")
NOUVEAU = reverse("client_nouveau")
SAISIE = {
    "nom": "Restaurant Le Baobab",
    "categorie": "restaurant",
    "telephone": "07 00 00 00 01",
    "conditions_paiement": "fin_de_mois",
    "adresse-libelle": "Cuisine",
    "adresse-adresse": "Face pharmacie",
    "adresse-commune": "Cocody",
    "contact-nom": "M. Koffi",
}


@pytest.fixture(autouse=True)
def sans_2fa_obligatoire(settings):
    settings.MIDDLEWARE = [
        m for m in settings.MIDDLEWARE if "DoubleAuthentificationObligatoire" not in m
    ]


@pytest.fixture
def gestionnaire(client):
    utilisateur = UtilisateurFactory(roles=[Role.GESTIONNAIRE])
    client.force_login(utilisateur)
    return utilisateur


@pytest.mark.parametrize(
    "role",
    [Role.DIRECTION, Role.GESTIONNAIRE, Role.COMMERCIAL, Role.FACTURATION, Role.COMPTABILITE],
)
def test_lecture_autorisee(client, role):
    utilisateur = UtilisateurFactory(roles=[role])
    fiche = ClientFactory(entreprise=utilisateur.entreprise)
    client.force_login(utilisateur)
    assert client.get(LISTE).status_code == 200
    assert client.get(reverse("client_detail", args=[fiche.pk])).status_code == 200


@pytest.mark.parametrize("role", [Role.PRODUCTION, Role.CHAUFFEUR, Role.PREPARATION])
def test_lecture_refusee(client, role):
    client.force_login(UtilisateurFactory(roles=[role]))
    assert client.get(LISTE).status_code == 403


@pytest.mark.parametrize("role", [Role.FACTURATION, Role.COMPTABILITE])
def test_creation_refusee_aux_roles_en_lecture(client, role):
    client.force_login(UtilisateurFactory(roles=[role]))
    assert "+ Nouveau" not in client.get(LISTE).content.decode()
    assert client.get(NOUVEAU).status_code == 403
    assert client.post(NOUVEAU, SAISIE).status_code == 403
    assert not Client.toutes_entreprises.exists()


def test_creation_par_le_commercial(client):
    commercial = UtilisateurFactory(roles=[Role.COMMERCIAL])
    client.force_login(commercial)
    assert "+ Nouveau" in client.get(LISTE).content.decode()
    reponse = client.post(NOUVEAU, SAISIE)
    fiche = Client.toutes_entreprises.get()
    assert reponse.url == reverse("client_detail", args=[fiche.pk])
    assert fiche.cree_par == commercial


@pytest.mark.parametrize("role", [Role.COMMERCIAL, Role.FACTURATION, Role.COMPTABILITE])
def test_modification_refusee_hors_direction_et_gestionnaire(client, role):
    utilisateur = UtilisateurFactory(roles=[role])
    fiche = ClientFactory(entreprise=utilisateur.entreprise)
    adresse = AdresseFactory(client=fiche, par_defaut=True)
    client.force_login(utilisateur)
    contenu = client.get(reverse("client_detail", args=[fiche.pk])).content.decode()
    assert "Modifier" not in contenu and "+ Ajouter une adresse" not in contenu
    assert client.get(reverse("client_modifier", args=[fiche.pk])).status_code == 403
    assert client.post(reverse("client_desactiver", args=[fiche.pk])).status_code == 403
    assert client.get(reverse("client_adresse_ajouter", args=[fiche.pk])).status_code == 403
    assert client.get(reverse("client_contact_ajouter", args=[fiche.pk])).status_code == 403
    url = reverse("client_adresse_par_defaut", args=[fiche.pk, adresse.pk])
    assert client.post(url).status_code == 403


def test_creation_par_le_gestionnaire(client, gestionnaire):
    reponse = client.post(NOUVEAU, SAISIE)
    fiche = Client.toutes_entreprises.get()
    assert reponse.url == reverse("client_detail", args=[fiche.pk])
    assert (fiche.code, fiche.entreprise, fiche.cree_par) == (
        "CLI-000001", gestionnaire.entreprise, gestionnaire,
    )  # fmt: skip
    contenu = client.get(reponse.url).content.decode()
    for texte in ["CLI-000001", "Restaurant Le Baobab", "Fin de mois", "Cocody", "M. Koffi"]:
        assert texte in contenu


def test_doublon_signale_puis_confirme(client, gestionnaire):
    client.post(NOUVEAU, SAISIE)
    reponse = client.post(NOUVEAU, SAISIE)
    assert reponse.status_code == 200
    assert "Un client porte déjà ce nom et ce téléphone" in reponse.content.decode()
    assert Client.toutes_entreprises.count() == 1

    client.post(NOUVEAU, {**SAISIE, "forcer_doublon": "on"})
    assert Client.toutes_entreprises.count() == 2


def test_erreurs_de_saisie_affichees(client, gestionnaire):
    reponse = client.post(NOUVEAU, {**SAISIE, "telephone": "", "adresse-commune": ""})
    assert reponse.status_code == 200
    assert reponse.content.decode().count("Ce champ est obligatoire") == 2
    assert not Client.toutes_entreprises.exists()


def test_fiche_d_une_autre_entreprise_introuvable(client, gestionnaire):
    ailleurs = ClientFactory(entreprise=EntrepriseFactory())
    assert client.get(reverse("client_detail", args=[ailleurs.pk])).status_code == 404
    assert client.post(reverse("client_desactiver", args=[ailleurs.pk])).status_code == 404


def test_liste_limitee_a_l_entreprise(client, gestionnaire):
    ClientFactory(entreprise=gestionnaire.entreprise, nom="Supérette Les Palmiers")
    ClientFactory(entreprise=EntrepriseFactory(), nom="Client d'ailleurs")
    contenu = client.get(LISTE).content.decode()
    assert "Supérette Les Palmiers" in contenu
    assert "ailleurs" not in contenu


def test_recherche_htmx(client, gestionnaire):
    ClientFactory(entreprise=gestionnaire.entreprise, nom="Café Le Plateau")
    ClientFactory(entreprise=gestionnaire.entreprise, nom="Hôtel Ivoire")
    reponse = client.get(
        LISTE, {"q": "plateau"}, headers={"HX-Request": "true", "HX-Target": "liste-clients"}
    )
    contenu = reponse.content.decode()
    assert 'id="liste-clients"' in contenu and "Navigation principale" not in contenu
    assert "Café Le Plateau" in contenu and "Hôtel Ivoire" not in contenu


def test_clients_desactives_masques_par_defaut(client, gestionnaire):
    ClientFactory(entreprise=gestionnaire.entreprise, nom="Ancien client", actif=False)
    assert "Ancien client" not in client.get(LISTE).content.decode()
    assert "Ancien client" in client.get(LISTE, {"inactifs": "1"}).content.decode()


def test_adresses_par_l_ecran(client, gestionnaire):
    fiche = ClientFactory(entreprise=gestionnaire.entreprise)
    principale = AdresseFactory(client=fiche, par_defaut=True)
    client.post(
        reverse("client_adresse_ajouter", args=[fiche.pk]),
        {"libelle": "Entrepôt", "adresse": "Zone 4", "commune": "Marcory"},
    )
    entrepot = fiche.adresses.model.toutes_entreprises.get(libelle="Entrepôt")

    reponse = client.post(
        reverse("client_adresse_desactiver", args=[fiche.pk, principale.pk]), follow=True
    )
    assert "Choisir une autre adresse par défaut" in reponse.content.decode()

    client.post(reverse("client_adresse_par_defaut", args=[fiche.pk, entrepot.pk]))
    entrepot.refresh_from_db()
    assert entrepot.par_defaut is True
