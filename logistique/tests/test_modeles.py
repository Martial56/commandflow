from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from entreprises.tests.factories import EntrepriseFactory
from logistique.models import (
    Chargement,
    HistoriqueChargement,
    LigneChargement,
    StatutChargement,
    StatutTournee,
    Tournee,
)
from logistique.tests.factories import (
    ChargementFactory,
    LigneChargementFactory,
    PrestataireFactory,
    TourneeFactory,
    VehiculeFactory,
)
from noyau.contexte import activer_entreprise

pytestmark = pytest.mark.django_db


def test_tournee_par_defaut_planifiee():
    tournee = TourneeFactory()
    assert tournee.statut == StatutTournee.PLANIFIEE
    assert tournee.livreur == tournee.chauffeur == tournee.compte_livreur


def test_chauffeur_ou_prestataire_pas_les_deux():
    tournee = TourneeFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        TourneeFactory(
            entreprise=tournee.entreprise,
            prestataire=PrestataireFactory(entreprise=tournee.entreprise),
        )


def test_prestataire_avec_ou_sans_compte():
    tournee = TourneeFactory(chauffeur=None)
    prestataire = PrestataireFactory(entreprise=tournee.entreprise)
    tournee.prestataire = prestataire
    tournee.save()
    assert tournee.livreur == prestataire and tournee.compte_livreur is None


def test_numero_unique_par_entreprise():
    tournee = TourneeFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        TourneeFactory(entreprise=tournee.entreprise, numero=tournee.numero)
    assert TourneeFactory(numero=tournee.numero).pk  # autre entreprise


def test_immatriculation_unique_par_entreprise():
    vehicule = VehiculeFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        VehiculeFactory(entreprise=vehicule.entreprise, immatriculation=vehicule.immatriculation)


def test_un_chargement_par_tournee_et_commande_sauf_retire():
    chargement = ChargementFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        ChargementFactory(tournee=chargement.tournee, commande=chargement.commande)
    Chargement.toutes_entreprises.filter(pk=chargement.pk).update(statut=StatutChargement.RETIRE)
    assert ChargementFactory(tournee=chargement.tournee, commande=chargement.commande).pk


def test_ecart_et_conformite_d_une_ligne():
    ligne = LigneChargement(quantite_attendue=60)
    assert ligne.ecart is None and not ligne.conforme
    ligne.quantite_preparee = 30
    assert ligne.ecart == Decimal("-30") and not ligne.conforme
    ligne.quantite_preparee = 60
    assert ligne.conforme


def test_quantites_negatives_refusees():
    ligne = LigneChargementFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        LigneChargement.toutes_entreprises.filter(pk=ligne.pk).update(quantite_preparee=-1)


def test_statuts_non_modifiables_directement():
    chargement = ChargementFactory()
    with pytest.raises(AttributeError):
        chargement.statut = StatutChargement.RECUE_CHAUFFEUR
    with pytest.raises(AttributeError):
        chargement.tournee.statut = StatutTournee.EN_COURS


def test_historique_des_chargements():
    chargement = ChargementFactory()
    assert HistoriqueChargement.objects.filter(pgh_obj=chargement).count() == 1


def test_isolation_entre_entreprises():
    tournee = TourneeFactory()
    TourneeFactory(entreprise=EntrepriseFactory())
    with activer_entreprise(tournee.entreprise):
        assert list(Tournee.objects.all()) == [tournee]
