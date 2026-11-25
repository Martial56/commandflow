"""Historique des prix des produits (lot B9), reconstruit depuis pghistory."""

import pgtrigger
import pytest

from catalogue import services
from catalogue.historique import historique_des_prix
from catalogue.models import Produit, Unite
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from noyau.contexte import activer_entreprise, activer_utilisateur

pytestmark = pytest.mark.django_db


@pytest.fixture
def contexte(utilisateur):
    with activer_entreprise(utilisateur.entreprise), activer_utilisateur(utilisateur):
        yield utilisateur


def _creer(**valeurs):
    donnees = {"code": "BAG", "nom": "Baguette", "unite": Unite.PIECE, "prix_reference": 150}
    donnees.update(valeurs)
    return services.creer_produit(**donnees)


def test_prix_successifs_du_plus_recent_avec_leur_auteur(contexte):
    produit = _creer()
    autre = UtilisateurFactory(entreprise=contexte.entreprise)
    services.modifier_produit(produit, prix_reference=175)
    with activer_utilisateur(autre):
        services.modifier_produit(Produit.objects.get(pk=produit.pk), prix_reference=200)
    historique = historique_des_prix([produit])[produit.pk]
    assert [(p.prix, p.auteur, p.creation) for p in historique.prix] == [
        (200, autre, False),
        (175, contexte, False),
        (150, contexte, True),
    ]
    assert historique.complet


def test_seuls_les_changements_de_prix_apparaissent(contexte):
    produit = _creer()
    services.modifier_produit(produit, nom="Pain baguette", taux_tva=18)
    services.desactiver_produit(Produit.objects.get(pk=produit.pk))
    historique = historique_des_prix([produit])[produit.pk]
    assert [p.prix for p in historique.prix] == [150]


def test_produit_anterieur_au_suivi(contexte):
    """Produit créé avant la migration 0003 : sa création n'est pas dans l'historique."""
    with pgtrigger.ignore("catalogue.Produit:insert_insert"):
        produit = _creer()
    historique = historique_des_prix([produit])[produit.pk]
    assert historique.prix == [] and not historique.complet
    services.modifier_produit(produit, prix_reference=175)
    historique = historique_des_prix([produit])[produit.pk]
    assert [p.prix for p in historique.prix] == [175] and not historique.complet


def test_isolation_entre_entreprises(contexte):
    produit = _creer()
    autre_entreprise = EntrepriseFactory()
    with activer_entreprise(autre_entreprise):
        etranger = _creer(code="CROI", nom="Croissant", prix_reference=300)
        services.modifier_produit(etranger, prix_reference=350)
    historiques = historique_des_prix(list(Produit.objects.all()))
    assert set(historiques) == {produit.pk}
    assert [p.prix for p in historiques[produit.pk].prix] == [150]
