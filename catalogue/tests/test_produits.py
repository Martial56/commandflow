from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from catalogue import services
from catalogue.models import Produit, Unite
from catalogue.tests.factories import ProduitFactory
from entreprises.tests.factories import EntrepriseFactory
from noyau.contexte import AucuneEntrepriseCourante, activer_entreprise, activer_utilisateur

pytestmark = pytest.mark.django_db


@pytest.fixture
def contexte(utilisateur):
    with activer_entreprise(utilisateur.entreprise), activer_utilisateur(utilisateur):
        yield utilisateur


def test_creation_dans_l_entreprise_courante(contexte):
    produit = services.creer_produit(
        code="BAG", nom="Pain baguette", unite=Unite.PIECE, prix_reference=250
    )
    assert produit.entreprise == contexte.entreprise
    assert produit.cree_par == contexte
    assert produit.actif is True


def test_creation_refusee_sans_entreprise():
    with pytest.raises(AucuneEntrepriseCourante):
        services.creer_produit(code="BAG", nom="Pain baguette", prix_reference=250)


def test_code_unique_dans_l_entreprise(contexte):
    services.creer_produit(code="BAG", nom="Pain baguette", prix_reference=250)
    with pytest.raises(ValidationError):
        services.creer_produit(code="BAG", nom="Autre", prix_reference=100)


def test_meme_code_possible_dans_une_autre_entreprise(contexte):
    services.creer_produit(code="BAG", nom="Pain baguette", prix_reference=250)
    with activer_entreprise(None):
        autre = ProduitFactory(entreprise=EntrepriseFactory(), code="BAG")
    assert autre.pk is not None


def test_prix_negatif_refuse(contexte):
    with pytest.raises(ValidationError):
        services.creer_produit(code="BAG", nom="Pain baguette", prix_reference=-1)
    produit = ProduitFactory(entreprise=contexte.entreprise)
    with pytest.raises(IntegrityError), transaction.atomic():
        Produit.objects.filter(pk=produit.pk).update(prix_reference=-1)


def test_modification(contexte):
    produit = ProduitFactory(entreprise=contexte.entreprise, prix_reference=250)
    produit = services.modifier_produit(produit, prix_reference=275, nom="Baguette")
    produit.refresh_from_db()
    assert (produit.prix_reference, produit.nom) == (275, "Baguette")


def test_modification_limitee_aux_champs_autorises(contexte):
    produit = ProduitFactory(entreprise=contexte.entreprise)
    with pytest.raises(ValueError):
        services.modifier_produit(produit, entreprise=EntrepriseFactory())


def test_desactivation_au_lieu_de_suppression(contexte):
    produit = ProduitFactory(entreprise=contexte.entreprise)
    services.desactiver_produit(produit)
    produit.refresh_from_db()
    assert produit.actif is False
    services.reactiver_produit(produit)
    produit.refresh_from_db()
    assert produit.actif is True


@pytest.mark.parametrize(
    ("unite", "quantite", "acceptee"),
    [
        (Unite.PIECE, 120, True),
        (Unite.PIECE, Decimal("1.5"), False),
        (Unite.CARTON, Decimal("2.000"), True),
        (Unite.KG, Decimal("1.250"), True),
        (Unite.KG, 0, False),
        (Unite.PIECE, -3, False),
    ],
)
def test_quantites_acceptees_selon_l_unite(unite, quantite, acceptee):
    assert Produit(unite=unite).accepte_quantite(quantite) is acceptee


def test_isolation_entre_entreprises(contexte):
    ProduitFactory(entreprise=contexte.entreprise, code="A")
    with activer_entreprise(None):
        ProduitFactory(entreprise=EntrepriseFactory(), code="B")
    assert list(Produit.objects.values_list("code", flat=True)) == ["A"]
