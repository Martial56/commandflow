"""Transitions du Sprint 3 (inclure au plan, lancer la préparation, marquer prête) :
un cas autorisé et des cas refusés pour chacune."""

import pytest
from django_fsm import TransitionNotAllowed, can_proceed, has_transition_perm

from commandes.models import Statut
from commandes.tests.factories import CommandeFactory
from commandes.tests.test_transitions import accepter, contexte, passer, utilisateur  # noqa: F401
from comptes.models import Role
from noyau.models import Validation
from production.models import StatutPlan
from production.tests.factories import BesoinProduitFactory, PlanProductionFactory

pytestmark = pytest.mark.django_db
R = Role


def plan_pour(commande, valide=False, **quantites):
    """Plan de la date de la commande, avec un besoin par produit de ses lignes."""
    plan = PlanProductionFactory(
        entreprise=commande.entreprise, date_livraison=commande.date_livraison
    )
    for ligne in commande.lignes_actives:
        BesoinProduitFactory(
            plan=plan, produit=ligne.produit, quantite_commandee=ligne.quantite, **quantites
        )
    if valide:
        plan.valider(par=utilisateur(commande, R.PRODUCTION))
        plan.save()
    return plan


@pytest.fixture
def confirmee(contexte):  # noqa: F811
    commande = CommandeFactory()
    contexte(commande)
    return passer(commande, ("soumettre", {}), ("confirmer", {}))


def en_preparation(commande, **quantites):
    passer(commande, ("inclure_au_plan", {}))
    plan = plan_pour(commande, valide=True, **quantites)
    passer(commande, ("lancer_preparation", {}))
    return plan


# --- inclure_au_plan -------------------------------------------------------------


def test_inclure_au_plan(confirmee):
    passer(confirmee, ("inclure_au_plan", {}))
    assert confirmee.statut == Statut.A_PLANIFIER


def test_inclure_au_plan_refuse_au_mauvais_statut(contexte):  # noqa: F811
    commande = CommandeFactory()
    contexte(commande)
    with pytest.raises(TransitionNotAllowed):
        commande.inclure_au_plan()


# --- lancer_preparation ----------------------------------------------------------


def test_lancer_preparation_exige_un_plan_valide(confirmee):
    passer(confirmee, ("inclure_au_plan", {}))
    assert not can_proceed(confirmee.lancer_preparation)  # aucun plan
    plan = plan_pour(confirmee)
    assert not can_proceed(confirmee.lancer_preparation)  # plan en brouillon
    plan.valider(par=utilisateur(confirmee, R.PRODUCTION))
    plan.save()
    passer(confirmee, ("lancer_preparation", {}))
    assert confirmee.statut == Statut.EN_PREPARATION
    assert plan.statut == StatutPlan.VALIDE


def test_lancer_preparation_refuse_au_mauvais_statut(confirmee):
    plan_pour(confirmee, valide=True)
    with pytest.raises(TransitionNotAllowed):
        confirmee.lancer_preparation()  # pas encore « à planifier »


# --- marquer_prete ---------------------------------------------------------------


@pytest.mark.parametrize("role", [R.PRODUCTION, R.GESTIONNAIRE])
def test_marquer_prete_quand_le_disponible_couvre(confirmee, role):
    en_preparation(confirmee, quantite_produite=1000)
    assert has_transition_perm(confirmee.marquer_prete, utilisateur(confirmee, role))
    passer(confirmee, ("marquer_prete", {}))
    assert confirmee.statut == Statut.PRETE_CHARGEMENT


@pytest.mark.parametrize("role", [R.COMMERCIAL, R.DIRECTION, R.CHAUFFEUR])
def test_marquer_prete_refuse_au_mauvais_role(confirmee, role):
    en_preparation(confirmee, quantite_produite=1000)
    assert not has_transition_perm(confirmee.marquer_prete, utilisateur(confirmee, role))


def test_marquer_prete_refuse_si_quantites_insuffisantes(confirmee):
    en_preparation(confirmee, quantite_produite=0)
    assert not can_proceed(confirmee.marquer_prete)


def test_marquer_prete_tient_compte_des_reservations(confirmee):
    plan = en_preparation(confirmee, quantite_produite=1000)
    ligne = confirmee.lignes_actives.first()
    besoin = plan.besoins.get(produit=ligne.produit)
    besoin.quantite_reservee = besoin.quantite_produite - ligne.quantite + 1
    besoin.save()
    assert not can_proceed(confirmee.marquer_prete)


def test_marquer_prete_par_exception_acceptee(confirmee):
    en_preparation(confirmee, quantite_produite=0)
    accepter(confirmee, Validation.Action.EXCEPTION, utilisateur(confirmee, R.DIRECTION))
    passer(confirmee, ("marquer_prete", {}))
    assert confirmee.statut == Statut.PRETE_CHARGEMENT


def test_marquer_prete_refuse_au_mauvais_statut(confirmee):
    with pytest.raises(TransitionNotAllowed):
        confirmee.marquer_prete()


# --- annulation validée jusqu'à « prête au chargement » ------------------------


@pytest.mark.parametrize(
    "statut", [Statut.A_PLANIFIER, Statut.EN_PREPARATION, Statut.PRETE_CHARGEMENT]
)
def test_annulation_validee_possible_jusqu_a_prete(contexte, statut):  # noqa: F811
    commande = CommandeFactory(statut=statut)
    contexte(commande)
    commande.motif_annulation = "Fermeture"
    assert not can_proceed(commande.annuler)
    accepter(commande, Validation.Action.ANNULATION, utilisateur(commande, R.DIRECTION))
    passer(commande, ("annuler", {}))
    assert commande.statut == Statut.ANNULEE
