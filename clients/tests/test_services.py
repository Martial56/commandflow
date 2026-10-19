import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from clients import services
from clients.models import Adresse, Categorie, Client, ConditionsPaiement
from clients.tests.factories import AdresseFactory, ClientFactory
from entreprises.tests.factories import EntrepriseFactory
from noyau.contexte import AucuneEntrepriseCourante, activer_entreprise, activer_utilisateur

pytestmark = pytest.mark.django_db

CLIENT = {
    "nom": "Restaurant Le Baobab",
    "categorie": Categorie.RESTAURANT,
    "telephone": "07 00 00 00 01",
}
ADRESSE = {"libelle": "Cuisine", "adresse": "Face pharmacie", "commune": "Cocody"}


@pytest.fixture
def contexte(utilisateur):
    with activer_entreprise(utilisateur.entreprise), activer_utilisateur(utilisateur):
        yield utilisateur


def test_creation_avec_code_adresse_et_contact(contexte):
    client = services.creer_client(
        adresse=ADRESSE,
        contact={"nom": "M. Koffi", "fonction": "gérant", "telephone": ""},
        **CLIENT,
    )
    assert client.code == "CLI-000001"
    assert client.entreprise == contexte.entreprise
    assert client.cree_par == contexte
    assert client.adresse_par_defaut.commune == "Cocody"
    assert client.contacts.get().nom == "M. Koffi"
    assert services.creer_client(
        **{**CLIENT, "nom": "Autre", "telephone": "01 02 03 04 05"}
    ).code == ("CLI-000002")


def test_creation_refusee_sans_entreprise():
    with pytest.raises(AucuneEntrepriseCourante):
        services.creer_client(**CLIENT)


@pytest.mark.parametrize("champ", ["nom", "categorie", "telephone"])
def test_champs_obligatoires(contexte, champ):
    with pytest.raises(ValidationError):
        services.creer_client(**{**CLIENT, champ: ""})


def test_delai_obligatoire_pour_paiement_a_x_jours(contexte):
    with pytest.raises(ValidationError):
        services.creer_client(conditions_paiement=ConditionsPaiement.X_JOURS, **CLIENT)
    client = services.creer_client(
        conditions_paiement=ConditionsPaiement.X_JOURS, delai_paiement_jours=30, **CLIENT
    )
    assert client.libelle_conditions() == "À 30 jours"


def test_doublon_signale_puis_force(contexte):
    services.creer_client(**CLIENT)
    variante = {**CLIENT, "nom": "restaurant  le BAOBAB", "telephone": "0700000001"}
    with pytest.raises(services.DoublonPotentiel) as doublon:
        services.creer_client(**variante)
    assert [c.nom for c in doublon.value.clients] == ["Restaurant Le Baobab"]
    assert services.creer_client(forcer_doublon=True, **variante).pk is not None


def test_pas_de_doublon_si_seul_le_nom_est_identique(contexte):
    services.creer_client(**CLIENT)
    assert services.creer_client(**{**CLIENT, "telephone": "05 11 22 33 44"}).pk is not None


def test_doublon_d_une_autre_entreprise_ignore(contexte):
    with activer_entreprise(None):
        ClientFactory(
            entreprise=EntrepriseFactory(), nom=CLIENT["nom"], telephone=CLIENT["telephone"]
        )
    assert services.creer_client(**CLIENT).pk is not None


def test_numero_non_consomme_si_la_creation_echoue(contexte):
    with pytest.raises(ValidationError):
        services.creer_client(adresse={"libelle": "Sans adresse ni commune"}, **CLIENT)
    assert services.creer_client(**CLIENT).code == "CLI-000001"
    assert Client.objects.count() == 1


def test_premiere_adresse_par_defaut_puis_changement(contexte):
    client = services.creer_client(**CLIENT)
    premiere = services.ajouter_adresse(client, **ADRESSE)
    seconde = services.ajouter_adresse(client, **{**ADRESSE, "libelle": "Entrepôt"})
    premiere.refresh_from_db()
    assert premiere.par_defaut and not seconde.par_defaut

    services.definir_adresse_par_defaut(seconde)
    premiere.refresh_from_db()
    seconde.refresh_from_db()
    assert seconde.par_defaut and not premiere.par_defaut
    assert client.adresse_par_defaut == seconde


def test_une_seule_adresse_par_defaut_en_base(contexte):
    adresse = AdresseFactory(client=ClientFactory(entreprise=contexte.entreprise), par_defaut=True)
    with pytest.raises(IntegrityError), transaction.atomic():
        AdresseFactory(client=adresse.client, par_defaut=True)


def test_adresse_par_defaut_non_retirable(contexte):
    client = services.creer_client(adresse=ADRESSE, **CLIENT)
    autre = services.ajouter_adresse(client, **{**ADRESSE, "libelle": "Entrepôt"})
    with pytest.raises(ValueError):
        services.desactiver_adresse(client.adresse_par_defaut)
    services.desactiver_adresse(autre)
    autre.refresh_from_db()
    assert autre.actif is False
    assert Adresse.objects.filter(pk=autre.pk).exists()


def test_desactivation_du_client(contexte):
    client = services.creer_client(**CLIENT)
    services.desactiver_client(client)
    client.refresh_from_db()
    assert client.actif is False
    services.reactiver_client(client)
    client.refresh_from_db()
    assert client.actif is True


def test_modification_limitee_aux_champs_autorises(contexte):
    client = services.creer_client(**CLIENT)
    services.modifier_client(client, ncc="CI-123")
    client.refresh_from_db()
    assert client.ncc == "CI-123"
    with pytest.raises(ValueError):
        services.modifier_client(client, code="CLI-999999")


def test_isolation_entre_entreprises(contexte):
    ClientFactory(entreprise=contexte.entreprise, nom="Chez nous")
    with activer_entreprise(None):
        ClientFactory(entreprise=EntrepriseFactory(), nom="Ailleurs")
    assert list(Client.objects.values_list("nom", flat=True)) == ["Chez nous"]
