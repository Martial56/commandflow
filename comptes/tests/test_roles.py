import pytest
from django.contrib.auth.models import AnonymousUser, Group

from comptes.models import Role
from comptes.rules import a_le_role, meme_entreprise
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from noyau.tests.models import ObjetTest

pytestmark = pytest.mark.django_db


def test_un_groupe_par_role():
    noms = set(Group.objects.values_list("name", flat=True))
    assert {role.label for role in Role} <= noms
    assert len(Role) == 9


def test_factory_ajoute_les_groupes_et_le_role_principal():
    chauffeur = UtilisateurFactory(roles=[Role.CHAUFFEUR])
    assert list(chauffeur.groups.values_list("name", flat=True)) == ["Chauffeur"]
    assert chauffeur.role_principal == Role.CHAUFFEUR


# a_le_role


def test_a_le_role_autorise():
    assert a_le_role(Role.DIRECTION)(UtilisateurFactory(roles=[Role.DIRECTION]))


def test_a_le_role_accepte_l_un_des_roles():
    gestionnaire = UtilisateurFactory(roles=[Role.GESTIONNAIRE])
    assert a_le_role(Role.DIRECTION, Role.GESTIONNAIRE)(gestionnaire)


def test_a_le_role_refuse_un_autre_role():
    assert not a_le_role(Role.DIRECTION)(UtilisateurFactory(roles=[Role.CHAUFFEUR]))


def test_a_le_role_refuse_un_compte_desactive():
    direction = UtilisateurFactory(roles=[Role.DIRECTION], is_active=False)
    assert not a_le_role(Role.DIRECTION)(direction)


def test_a_le_role_refuse_un_visiteur_anonyme():
    assert not a_le_role(Role.DIRECTION)(AnonymousUser())


def test_plusieurs_roles_par_utilisateur():
    polyvalent = UtilisateurFactory(roles=[Role.PREPARATION, Role.CHAUFFEUR])
    assert a_le_role(Role.PREPARATION)(polyvalent)
    assert a_le_role(Role.CHAUFFEUR)(polyvalent)
    assert polyvalent.role_principal == Role.PREPARATION


# meme_entreprise


def test_meme_entreprise_autorise(utilisateur):
    objet = ObjetTest.toutes_entreprises.create(entreprise=utilisateur.entreprise, nom="x")
    assert meme_entreprise(utilisateur, objet)


def test_meme_entreprise_refuse_une_autre_entreprise(utilisateur):
    objet = ObjetTest.toutes_entreprises.create(entreprise=EntrepriseFactory(), nom="x")
    assert not meme_entreprise(utilisateur, objet)


def test_meme_entreprise_refuse_sans_entreprise():
    plateforme = UtilisateurFactory(entreprise=None, is_superuser=True)
    objet = ObjetTest.toutes_entreprises.create(entreprise=EntrepriseFactory(), nom="x")
    assert not meme_entreprise(plateforme, objet)
    assert not meme_entreprise(plateforme, None)
