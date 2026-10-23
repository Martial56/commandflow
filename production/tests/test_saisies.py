"""Saisies de production (étape 5) : cumul sans écrasement, annulation tracée (§9, 8.2)."""

import datetime
from contextlib import ExitStack, contextmanager
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from catalogue.models import Unite
from catalogue.tests.factories import ProduitFactory
from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from noyau.contexte import activer_entreprise, activer_utilisateur
from production import services
from production.models import (
    BesoinProduit,
    HistoriqueSaisieProduction,
    PlanProduction,
    SaisieProduction,
    StatutPlan,
)
from production.tests.factories import BesoinProduitFactory, PlanProductionFactory

pytestmark = pytest.mark.django_db
DEMAIN = datetime.date.today() + datetime.timedelta(days=1)


@pytest.fixture
def besoin():
    """Pain baguette de la maquette 2 : 1 400 à produire, plan validé."""
    plan = PlanProductionFactory(date_livraison=DEMAIN)
    besoin = BesoinProduitFactory(
        plan=plan,
        produit__nom="Pain baguette",
        quantite_commandee=1330,
        quantite_a_produire=1400,
    )
    with activer_entreprise(plan.entreprise):
        plan.valider(par=UtilisateurFactory(entreprise=plan.entreprise))
        plan.save()
    return besoin


@contextmanager
def en_tant_que(besoin, role, personne=None):
    personne = personne or UtilisateurFactory(entreprise=besoin.entreprise, roles=[role])
    with ExitStack() as pile:
        pile.enter_context(activer_entreprise(besoin.entreprise))
        pile.enter_context(activer_utilisateur(personne))
        yield personne


def relire(besoin):
    return BesoinProduit.toutes_entreprises.get(pk=besoin.pk)


# --- enregistrer_saisie ------------------------------------------------------------


def test_22_15_saisies_cumulees_sans_ecraser(besoin):
    """§22-15 : les quantités produites sont cumulées sans écraser les saisies précédentes."""
    with en_tant_que(besoin, Role.PRODUCTION) as atelier:
        for lot, quantite in [("Lot 1 · four n°1", 400), ("Lot 2 · four n°2", 380), ("", 200)]:
            services.enregistrer_saisie(besoin, quantite, lot=lot)
    besoin = relire(besoin)
    assert besoin.quantite_produite == 980
    assert besoin.quantite_manquante == 420  # maquette 2 : produit 980, manque 420
    saisies = list(SaisieProduction.toutes_entreprises.filter(besoin=besoin))
    assert [s.quantite for s in saisies] == [400, 380, 200]
    assert [s.lot for s in saisies][2] == "Lot 3"  # prérempli « Lot N+1 »
    assert {s.saisie_par for s in saisies} == {atelier}


@pytest.mark.parametrize("role", [Role.PRODUCTION, Role.GESTIONNAIRE])
def test_saisie_autorisee(besoin, role):
    with en_tant_que(besoin, role):
        services.enregistrer_saisie(besoin, 200, lot="Lot 1")
    assert relire(besoin).quantite_produite == 200


@pytest.mark.parametrize("role", [Role.DIRECTION, Role.COMMERCIAL, Role.PREPARATION])
def test_saisie_refusee_aux_autres_roles(besoin, role):
    with en_tant_que(besoin, role), pytest.raises(PermissionDenied):
        services.enregistrer_saisie(besoin, 200)
    assert not SaisieProduction.toutes_entreprises.exists()


def test_saisie_refusee_sur_un_plan_non_valide():
    besoin = BesoinProduitFactory()
    with en_tant_que(besoin, Role.PRODUCTION), pytest.raises(services.ActionImpossible):
        services.enregistrer_saisie(besoin, 10)


def test_saisie_refusee_sur_un_plan_cloture(besoin):
    with en_tant_que(besoin, Role.PRODUCTION):
        services.cloturer_plan(besoin.plan)
        with pytest.raises(services.ActionImpossible, match="clôturé"):
            services.enregistrer_saisie(besoin, 10)


def test_quantite_selon_l_unite(besoin):
    au_kg = BesoinProduitFactory(
        plan=besoin.plan, produit=ProduitFactory(entreprise=besoin.entreprise, unite=Unite.KG)
    )
    with en_tant_que(besoin, Role.PRODUCTION):
        for invalide in (0, -5, "2.5"):
            with pytest.raises(ValidationError):
                services.enregistrer_saisie(besoin, invalide)
        services.enregistrer_saisie(au_kg, "2.5")
    assert relire(au_kg).quantite_produite == Decimal("2.5")


def test_saisie_annulee_avec_la_transaction_appelante(besoin):
    from django.db import transaction

    with en_tant_que(besoin, Role.PRODUCTION), pytest.raises(RuntimeError):
        with transaction.atomic():
            services.enregistrer_saisie(besoin, 200)
            raise RuntimeError("erreur dans l'appelant")
    assert relire(besoin).quantite_produite == 0
    assert not SaisieProduction.toutes_entreprises.exists()


# --- annuler_saisie ----------------------------------------------------------------


def test_annulation_par_l_auteur(besoin):
    with en_tant_que(besoin, Role.PRODUCTION) as atelier:
        services.enregistrer_saisie(besoin, 400)
        erreur = services.enregistrer_saisie(besoin, 4000)  # zéro de trop
        services.annuler_saisie(erreur, "zéro de trop")
    erreur.refresh_from_db()
    assert (erreur.annulee, erreur.annulee_par, erreur.quantite) == (True, atelier, 4000)
    assert relire(besoin).quantite_produite == 400
    # L'erreur reste visible dans l'historique.
    assert HistoriqueSaisieProduction.objects.filter(pgh_obj=erreur).count() == 2


def test_annulation_par_un_responsable(besoin):
    with en_tant_que(besoin, Role.PRODUCTION):
        saisie = services.enregistrer_saisie(besoin, 400)
    with en_tant_que(besoin, Role.DIRECTION):
        services.annuler_saisie(saisie, "doublon")
    assert relire(besoin).quantite_produite == 0


def test_annulation_refusee_a_un_autre_membre_de_l_atelier(besoin):
    with en_tant_que(besoin, Role.PRODUCTION):
        saisie = services.enregistrer_saisie(besoin, 400)
    collegue = UtilisateurFactory(entreprise=besoin.entreprise, roles=[Role.PREPARATION])
    with en_tant_que(besoin, None, collegue), pytest.raises(PermissionDenied):
        services.annuler_saisie(saisie, "erreur")
    assert relire(besoin).quantite_produite == 400


def test_annulation_exige_un_motif_et_une_seule_fois(besoin):
    with en_tant_que(besoin, Role.PRODUCTION):
        saisie = services.enregistrer_saisie(besoin, 400)
        with pytest.raises(ValidationError):
            services.annuler_saisie(saisie, "  ")
        services.annuler_saisie(saisie, "erreur")
        with pytest.raises(services.ActionImpossible):
            services.annuler_saisie(saisie, "encore")


def test_nouvelle_saisie_apres_correction(besoin):
    """Une erreur se corrige par annulation puis nouvelle saisie (§3.4)."""
    with en_tant_que(besoin, Role.PRODUCTION):
        erreur = services.enregistrer_saisie(besoin, 4000)
        services.annuler_saisie(erreur, "zéro de trop")
        services.enregistrer_saisie(besoin, 400)
    assert relire(besoin).quantite_produite == 400


def test_recalcul_du_plan_conserve_le_cumul(besoin):
    with en_tant_que(besoin, Role.PRODUCTION):
        services.enregistrer_saisie(besoin, 300)
        services.recalculer_besoin(besoin.plan)
    assert relire(besoin).quantite_produite == 300


def test_saisie_d_une_autre_entreprise_refusee(besoin):
    ailleurs = EntrepriseFactory()
    personne = UtilisateurFactory(entreprise=ailleurs, roles=[Role.PRODUCTION])
    with activer_entreprise(ailleurs), activer_utilisateur(personne):
        with pytest.raises(PlanProduction.DoesNotExist):
            services.enregistrer_saisie(besoin, 10)
    assert PlanProduction.toutes_entreprises.get(pk=besoin.plan_id).statut == StatutPlan.VALIDE


def test_annulation_refusee_apres_cloture(besoin):
    with en_tant_que(besoin, Role.PRODUCTION):
        saisie = services.enregistrer_saisie(besoin, 400)
        services.cloturer_plan(besoin.plan)
        with pytest.raises(services.ActionImpossible, match="clôturé"):
            services.annuler_saisie(saisie, "erreur")
    assert relire(besoin).quantite_produite == 400
