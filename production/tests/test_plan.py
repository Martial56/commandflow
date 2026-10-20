"""Ajustement, validation et clôture du plan (étape 4, décisions 8.2)."""

import datetime

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from catalogue.models import Unite
from catalogue.tests.factories import ProduitFactory
from commandes.models import Commande, Statut
from commandes.tests.factories import CommandeFactory
from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from noyau.contexte import activer_entreprise, activer_utilisateur
from production import services
from production.models import (
    BesoinProduit,
    HistoriqueBesoinProduit,
    PlanProduction,
    StatutPlan,
)
from production.tests.factories import BesoinProduitFactory, PlanProductionFactory

pytestmark = pytest.mark.django_db
DEMAIN = datetime.date.today() + datetime.timedelta(days=1)
RESPONSABLES = [Role.PRODUCTION, Role.GESTIONNAIRE, Role.DIRECTION]


@pytest.fixture
def plan():
    plan = PlanProductionFactory(date_livraison=DEMAIN)
    BesoinProduitFactory(plan=plan, quantite_commandee=1330, quantite_a_produire=1330)
    return plan


@pytest.fixture
def en_tant_que():
    """`with en_tant_que(plan, role) as personne:` entreprise et utilisateur courants."""
    from contextlib import ExitStack, contextmanager

    @contextmanager
    def activer(plan, role):
        personne = UtilisateurFactory(entreprise=plan.entreprise, roles=[role])
        with ExitStack() as pile:
            pile.enter_context(activer_entreprise(plan.entreprise))
            pile.enter_context(activer_utilisateur(personne))
            yield personne

    return activer


def besoin_de(plan):
    return BesoinProduit.toutes_entreprises.filter(plan=plan).first()


def statut(plan):
    return PlanProduction.toutes_entreprises.get(pk=plan.pk).statut


# --- ajuster_a_produire ------------------------------------------------------------


@pytest.mark.parametrize("role", RESPONSABLES)
def test_ajuster_a_produire_par_un_responsable(plan, en_tant_que, role):
    with en_tant_que(plan, role) as personne:
        besoin = services.ajuster_a_produire(besoin_de(plan), 1400)
    assert (besoin.quantite_a_produire, besoin.marge) == (1400, 70)
    dernier = HistoriqueBesoinProduit.objects.filter(pgh_obj=besoin).latest("pgh_id")
    assert dernier.modifie_par == personne


@pytest.mark.parametrize("role", [Role.COMMERCIAL, Role.FACTURATION, Role.CHAUFFEUR])
def test_ajuster_refuse_aux_autres_roles(plan, en_tant_que, role):
    with en_tant_que(plan, role), pytest.raises(PermissionDenied):
        services.ajuster_a_produire(besoin_de(plan), 1400)


def test_ajuster_apres_validation_mais_pas_apres_cloture(plan, en_tant_que):
    with en_tant_que(plan, Role.PRODUCTION):
        services.valider_plan(plan)
        assert services.ajuster_a_produire(besoin_de(plan), 1350).quantite_a_produire == 1350
        services.cloturer_plan(plan)
        with pytest.raises(services.ActionImpossible):
            services.ajuster_a_produire(besoin_de(plan), 1400)


def test_ajuster_respecte_l_unite_du_produit(plan, en_tant_que):
    besoin = besoin_de(plan)
    au_kg = BesoinProduitFactory(
        plan=plan, produit=ProduitFactory(entreprise=plan.entreprise, unite=Unite.KG)
    )
    with en_tant_que(plan, Role.PRODUCTION):
        with pytest.raises(ValidationError):
            services.ajuster_a_produire(besoin, "12.5")
        with pytest.raises(ValidationError):
            services.ajuster_a_produire(besoin, -1)
        assert services.ajuster_a_produire(au_kg, "12.5").quantite_a_produire == 12.5
        assert services.ajuster_a_produire(besoin, 0).quantite_a_produire == 0


# --- valider_plan ------------------------------------------------------------------


@pytest.mark.parametrize("role", RESPONSABLES)
def test_valider_le_plan(plan, en_tant_que, role):
    with en_tant_que(plan, role) as personne:
        services.valider_plan(plan)
    plan.refresh_from_db()
    assert (plan.statut, plan.valide_par) == (StatutPlan.VALIDE, personne)


@pytest.mark.parametrize("role", [Role.COMMERCIAL, Role.PREPARATION, Role.COMPTABILITE])
def test_valider_refuse_aux_autres_roles(plan, en_tant_que, role):
    with en_tant_que(plan, role), pytest.raises(PermissionDenied):
        services.valider_plan(plan)
    assert statut(plan) == StatutPlan.BROUILLON


def test_valider_refuse_un_plan_vide(en_tant_que):
    plan = PlanProductionFactory()
    with en_tant_que(plan, Role.PRODUCTION), pytest.raises(services.ActionImpossible):
        services.valider_plan(plan)


def test_valider_refuse_un_plan_deja_valide(plan, en_tant_que):
    with en_tant_que(plan, Role.PRODUCTION):
        services.valider_plan(plan)
        with pytest.raises(services.ActionImpossible):
            services.valider_plan(plan)


def _commande(plan, statut, date=DEMAIN):
    return CommandeFactory(
        adresse__client__entreprise=plan.entreprise, statut=statut, date_livraison=date
    )


def test_validation_lance_la_preparation_des_commandes(plan, en_tant_que):
    a_planifier = [_commande(plan, Statut.A_PLANIFIER) for _ in range(2)]
    autre_jour = _commande(plan, Statut.A_PLANIFIER, DEMAIN + datetime.timedelta(days=3))
    a_confirmer = _commande(plan, Statut.A_CONFIRMER)
    with en_tant_que(plan, Role.PRODUCTION):
        services.valider_plan(plan)
    statuts = dict(Commande.toutes_entreprises.values_list("pk", "statut"))
    assert {statuts[c.pk] for c in a_planifier} == {Statut.EN_PREPARATION}
    assert statuts[autre_jour.pk] == Statut.A_PLANIFIER
    assert statuts[a_confirmer.pk] == Statut.A_CONFIRMER


def test_validation_annulee_si_une_commande_echoue(plan, en_tant_que, monkeypatch):
    commande = _commande(plan, Statut.A_PLANIFIER)

    def panne(self, *args, **kwargs):
        raise RuntimeError("panne")

    monkeypatch.setattr(type(commande), "lancer_preparation", panne)
    with en_tant_que(plan, Role.PRODUCTION), pytest.raises(RuntimeError):
        services.valider_plan(plan)
    assert statut(plan) == StatutPlan.BROUILLON
    assert Commande.toutes_entreprises.get(pk=commande.pk).statut == Statut.A_PLANIFIER


# --- cloturer_plan -----------------------------------------------------------------


def test_cloturer_un_plan_valide(plan, en_tant_que):
    with en_tant_que(plan, Role.GESTIONNAIRE) as personne:
        services.valider_plan(plan)
        services.cloturer_plan(plan)
    plan.refresh_from_db()
    assert (plan.statut, plan.cloture_par) == (StatutPlan.CLOTURE, personne)


def test_cloturer_refuse_un_brouillon(plan, en_tant_que):
    with en_tant_que(plan, Role.PRODUCTION), pytest.raises(services.ActionImpossible):
        services.cloturer_plan(plan)


def test_cloturer_refuse_aux_autres_roles(plan, en_tant_que):
    with en_tant_que(plan, Role.PRODUCTION):
        services.valider_plan(plan)
    with en_tant_que(plan, Role.COMMERCIAL), pytest.raises(PermissionDenied):
        services.cloturer_plan(plan)


def test_plan_d_une_autre_entreprise_refuse(plan):
    ailleurs = EntrepriseFactory()
    personne = UtilisateurFactory(entreprise=ailleurs, roles=[Role.PRODUCTION])
    with activer_entreprise(ailleurs), activer_utilisateur(personne):
        with pytest.raises(PlanProduction.DoesNotExist):
            services.valider_plan(plan)
