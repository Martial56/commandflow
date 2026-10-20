import datetime
from decimal import Decimal

import pytest
from django.db import IntegrityError, InternalError, ProgrammingError, transaction
from django_fsm import TransitionNotAllowed, can_proceed

from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from noyau.contexte import activer_entreprise
from production.models import BesoinProduit, PlanProduction, SaisieProduction, StatutPlan
from production.tests.factories import (
    BesoinProduitFactory,
    PlanProductionFactory,
    SaisieProductionFactory,
)

pytestmark = pytest.mark.django_db
DEMAIN = datetime.date.today() + datetime.timedelta(days=1)


def test_un_plan_par_date_et_par_entreprise():
    plan = PlanProductionFactory(date_livraison=DEMAIN)
    with pytest.raises(IntegrityError), transaction.atomic():
        PlanProductionFactory(entreprise=plan.entreprise, date_livraison=DEMAIN)
    assert PlanProductionFactory(date_livraison=DEMAIN).pk  # autre entreprise


def test_un_besoin_par_produit_dans_un_plan():
    besoin = BesoinProduitFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        BesoinProduitFactory(plan=besoin.plan, produit=besoin.produit)


def test_quantites_negatives_refusees():
    with pytest.raises(IntegrityError), transaction.atomic():
        BesoinProduitFactory(quantite_a_produire=-1)


@pytest.mark.parametrize(
    "commande, a_produire, produite, reservee, marge, manquante, disponible",
    [
        (1330, 1400, 980, 0, 70, 420, 980),
        (320, 320, 320, 120, 0, 0, 200),
        (150, 150, 200, 150, 0, 0, 50),
    ],
)
def test_quantites_derivees(commande, a_produire, produite, reservee, marge, manquante, disponible):
    besoin = BesoinProduit(
        quantite_commandee=commande,
        quantite_a_produire=a_produire,
        quantite_produite=produite,
        quantite_reservee=reservee,
    )
    assert besoin.marge == marge
    assert besoin.quantite_manquante == manquante
    assert besoin.quantite_disponible == disponible


def test_complet_seulement_si_objectif_atteint():
    assert BesoinProduit(quantite_a_produire=320, quantite_produite=320).complet
    assert not BesoinProduit(quantite_a_produire=150, quantite_produite=60).complet
    assert not BesoinProduit(quantite_a_produire=0, quantite_produite=0).complet


def test_transitions_du_plan():
    plan = PlanProductionFactory()
    responsable = UtilisateurFactory(entreprise=plan.entreprise)
    with activer_entreprise(plan.entreprise):
        _transitions(plan, responsable)


def _transitions(plan, responsable):
    assert not can_proceed(plan.valider)  # aucun besoin
    with pytest.raises(TransitionNotAllowed):
        plan.cloturer(par=responsable)  # pas encore validé

    BesoinProduitFactory(plan=plan, quantite_commandee=10)
    plan.valider(par=responsable)
    plan.save()
    assert plan.statut == StatutPlan.VALIDE and plan.valide_par == responsable
    with pytest.raises(TransitionNotAllowed):
        plan.valider(par=responsable)

    plan.cloturer(par=responsable)
    plan.save()
    assert plan.statut == StatutPlan.CLOTURE and plan.cloture_le is not None


def test_statut_non_modifiable_directement():
    plan = PlanProductionFactory()
    with pytest.raises(AttributeError):
        plan.statut = StatutPlan.VALIDE


def test_saisie_jamais_modifiee_en_base():
    saisie = SaisieProductionFactory(quantite=200)
    with pytest.raises((InternalError, ProgrammingError)), transaction.atomic():
        SaisieProduction.toutes_entreprises.filter(pk=saisie.pk).update(quantite=Decimal("300"))
    saisie.refresh_from_db()
    assert saisie.quantite == 200


def test_annulation_possible_avec_motif_seulement():
    saisie = SaisieProductionFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        SaisieProduction.toutes_entreprises.filter(pk=saisie.pk).update(annulee=True)
    SaisieProduction.toutes_entreprises.filter(pk=saisie.pk).update(
        annulee=True, motif_annulation="erreur de saisie"
    )
    saisie.refresh_from_db()
    assert saisie.annulee


def test_quantite_de_saisie_positive():
    with pytest.raises(IntegrityError), transaction.atomic():
        SaisieProductionFactory(quantite=0)


def test_historique_des_saisies():
    from production.models import HistoriqueSaisieProduction

    saisie = SaisieProductionFactory()
    assert HistoriqueSaisieProduction.objects.filter(pgh_obj=saisie).count() == 1


def test_isolation_entre_entreprises():
    plan = PlanProductionFactory()
    autre = PlanProductionFactory(entreprise=EntrepriseFactory())
    with activer_entreprise(plan.entreprise):
        assert list(PlanProduction.objects.all()) == [plan]
    assert autre.pk
