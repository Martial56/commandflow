import pytest

from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from noyau.contexte import (
    AucuneEntrepriseCourante,
    AutreEntreprise,
    activer_entreprise,
    activer_utilisateur,
)
from noyau.tests.models import ObjetTest

pytestmark = pytest.mark.django_db


@pytest.fixture
def entreprise_a():
    return EntrepriseFactory(nom="A")


@pytest.fixture
def entreprise_b():
    return EntrepriseFactory(nom="B")


@pytest.fixture
def objets(entreprise_a, entreprise_b):
    objet_a = ObjetTest.toutes_entreprises.create(entreprise=entreprise_a, nom="objet A")
    objet_b = ObjetTest.toutes_entreprises.create(entreprise=entreprise_b, nom="objet B")
    return objet_a, objet_b


# Isolation multi-entreprise


def test_lecture_limitee_a_l_entreprise_courante(entreprise_a, objets):
    objet_a, objet_b = objets
    with activer_entreprise(entreprise_a):
        assert list(ObjetTest.objects.all()) == [objet_a]
        with pytest.raises(ObjetTest.DoesNotExist):
            ObjetTest.objects.get(pk=objet_b.pk)


def test_lecture_refusee_sans_entreprise_courante(objets):
    with pytest.raises(AucuneEntrepriseCourante):
        ObjetTest.objects.all()


def test_gestionnaire_toutes_entreprises_sans_filtre(objets):
    assert ObjetTest.toutes_entreprises.count() == 2


def test_creation_rattachee_a_l_entreprise_courante(entreprise_a):
    with activer_entreprise(entreprise_a):
        objet = ObjetTest.objects.create(nom="nouveau")
    assert objet.entreprise == entreprise_a


def test_ecriture_refusee_dans_une_autre_entreprise(entreprise_a, objets):
    _, objet_b = objets
    objet_b.nom = "modifié"
    with activer_entreprise(entreprise_a), pytest.raises(AutreEntreprise):
        objet_b.save()


# Traçabilité


def test_auteurs_de_creation_et_de_modification(entreprise_a):
    createur = UtilisateurFactory(entreprise=entreprise_a)
    modificateur = UtilisateurFactory(entreprise=entreprise_a)
    with activer_entreprise(entreprise_a):
        with activer_utilisateur(createur):
            objet = ObjetTest.objects.create(nom="tracé")
        assert objet.cree_par == createur
        assert objet.modifie_par == createur
        assert objet.cree_le is not None

        with activer_utilisateur(modificateur):
            objet.nom = "modifié"
            objet.save(update_fields=["nom"])
        objet.refresh_from_db()
        assert objet.cree_par == createur
        assert objet.modifie_par == modificateur
        assert objet.modifie_le >= objet.cree_le
