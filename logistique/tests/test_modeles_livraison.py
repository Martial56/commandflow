"""Modèles de la livraison (§3.5, §6, décisions 8.3 et 8.4) : lignes prévues au départ,
contraintes sur les quantités, isolation entre entreprises."""

from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from catalogue.tests.factories import ProduitFactory
from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from logistique import services
from logistique.etat import exceptions
from logistique.models import (
    EtatRetour,
    Incident,
    LigneLivraison,
    Livraison,
    MotifEcart,
    Reliquat,
    Retour,
    TypeIncident,
)
from logistique.tests.factories import TourneeFactory
from logistique.tests.test_depart import demarrer, recue
from logistique.tests.test_services_controle import (
    EN_ATTENTE,
    affecter,
    compter,
    recues,
)
from logistique.tests.test_services_tournee import DEMAIN
from noyau.contexte import activer_entreprise, activer_utilisateur

pytestmark = pytest.mark.django_db


@pytest.fixture
def entreprise():
    entreprise = EntrepriseFactory()
    with activer_entreprise(entreprise):
        yield entreprise


@pytest.fixture
def tournee(entreprise, personnes):
    return TourneeFactory(entreprise=entreprise, date=DEMAIN)


def prevues(chargement):
    livraison = Livraison.objects.get(chargement=chargement)
    return [ligne.quantite_prevue for ligne in livraison.lignes.order_by("pk")]


def test_depart_cree_les_lignes_prevues(tournee, personnes):
    chargement = recue(tournee, personnes)
    demarrer(tournee)
    livraison = Livraison.objects.get(chargement=chargement)
    lignes = list(livraison.lignes.order_by("pk"))
    assert [ligne.ligne_commande for ligne in lignes] == [
        ligne.ligne_commande for ligne in chargement.lignes.order_by("pk")
    ]
    assert prevues(chargement) == [Decimal(60), Decimal(20)]
    assert all(ligne.quantite_livree is None and ligne.manque is None for ligne in lignes)


def test_prevu_issu_de_l_ecart_accepte(tournee, personnes):
    """Décision 8.3 : les quantités réellement chargées servent de « prévu »."""
    chargement = affecter(tournee, personnes)
    compter(chargement, [30, 20], personnes.preparateur, motif=MotifEcart.PRODUCTION_INSUFFISANTE)
    with activer_utilisateur(personnes.preparateur):
        services.signaler_ecart(chargement)
    demande = exceptions(chargement, "ecart", EN_ATTENTE).get()
    with activer_utilisateur(personnes.gestionnaire):
        services.decider_exception_chargement(demande, accepter=True)
    with activer_utilisateur(tournee.chauffeur):
        services.confirmer_reception(chargement, recues(chargement, [30, 20]))
    demarrer(tournee)
    assert prevues(chargement) == [Decimal(30), Decimal(20)]


def test_prevu_sans_comptage_part_de_l_attendu(tournee, personnes):
    """Départ par exception sans contrôle : à défaut de quantité chargée, l'attendu."""
    recue(tournee, personnes)  # le chargement de la tournée a commencé
    chargement = affecter(tournee, personnes)
    with activer_utilisateur(personnes.gestionnaire):
        demande = services.demander_exception_depart(chargement, "Client prévenu")
    with activer_utilisateur(personnes.direction):
        services.decider_exception_chargement(demande, accepter=True)
    demarrer(tournee)
    assert prevues(chargement) == [Decimal(60), Decimal(20)]


@pytest.fixture
def ligne(tournee, personnes):
    recue(tournee, personnes)
    demarrer(tournee)
    return LigneLivraison.objects.order_by("pk").first()


@pytest.mark.parametrize("livre", [Decimal(61), Decimal(-1)])
def test_livre_hors_limites_refuse(ligne, livre):
    """Décision 8.4 : jamais plus que le prévu, jamais négatif."""
    ligne.quantite_livree = livre
    with pytest.raises(IntegrityError), transaction.atomic():
        ligne.save()


def test_livre_dans_les_limites(ligne):
    ligne.quantite_livree = Decimal(45)
    ligne.save()
    ligne.refresh_from_db()
    assert ligne.manque == Decimal(15)


def test_retour_et_reliquat_quantite_positive(entreprise, ligne):
    livraison = ligne.livraison
    produit = ProduitFactory(entreprise=entreprise)
    with pytest.raises(IntegrityError), transaction.atomic():
        Retour.objects.create(
            livraison=livraison, produit=produit, quantite=0, motif="x", etat=EtatRetour.ABIME
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        Reliquat.objects.create(
            commande_origine=livraison.commande,
            livraison=livraison,
            ligne_commande=ligne.ligne_commande,
            quantite=0,
        )


def test_isolation_entre_entreprises(tournee, ligne):
    Incident.objects.create(tournee=tournee, type=TypeIncident.PANNE)
    with activer_entreprise(None):
        autre = EntrepriseFactory()
        UtilisateurFactory(entreprise=autre, roles=[Role.CHAUFFEUR])
    with activer_entreprise(autre):
        for modele in (Livraison, LigneLivraison, Incident):
            assert not modele.objects.exists()
