"""Cascade commandes → plan de production (§7) : intégration et recalcul des besoins."""

import datetime
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from unittest import mock

import pytest
from django.db import connection

from catalogue.tests.factories import ProduitFactory
from clients.tests.factories import AdresseFactory
from commandes import services as commandes
from commandes.models import Canal, Statut
from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from noyau.contexte import activer_entreprise, activer_utilisateur
from noyau.models import Validation
from production import services
from production.models import BesoinProduit, PlanProduction, StatutPlan

pytestmark = pytest.mark.django_db
DEMAIN = datetime.date.today() + datetime.timedelta(days=1)
APRES_DEMAIN = DEMAIN + datetime.timedelta(days=1)


@pytest.fixture
def entreprise():
    return EntrepriseFactory()


@pytest.fixture
def personnes(entreprise):
    return {
        role: UtilisateurFactory(entreprise=entreprise, roles=[role])
        for role in (Role.COMMERCIAL, Role.GESTIONNAIRE, Role.DIRECTION, Role.PRODUCTION)
    }


@pytest.fixture
def produits(entreprise):
    return {
        "baguette": ProduitFactory(entreprise=entreprise, nom="Pain baguette"),
        "croissant": ProduitFactory(entreprise=entreprise, nom="Croissant"),
    }


@pytest.fixture
def en_tant_que(entreprise, personnes):
    def activer(role):
        pile = [activer_entreprise(entreprise), activer_utilisateur(personnes[role])]
        for gestionnaire in pile:
            gestionnaire.__enter__()
        return pile

    piles = []
    yield lambda role: piles.append(activer(role))
    for pile in reversed(piles):
        for gestionnaire in reversed(pile):
            gestionnaire.__exit__(None, None, None)


def nouvelle(entreprise, lignes, date=DEMAIN):
    adresse = AdresseFactory(client__entreprise=entreprise, par_defaut=True)
    commande = commandes.creer_commande(
        client=adresse.client,
        lignes=lignes,
        canal=Canal.TELEPHONE,
        date_livraison=date,
        creneau="06h–08h",
    )
    commandes.soumettre_commande(commande)
    return commande


def confirmee(entreprise, lignes, date=DEMAIN):
    return commandes.confirmer_commande(nouvelle(entreprise, lignes, date))


def besoins(date=DEMAIN):
    plan = PlanProduction.objects.get(date_livraison=date)
    return {
        b.produit.nom: (b.quantite_commandee, b.quantite_a_produire)
        for b in BesoinProduit.objects.filter(plan=plan).select_related("produit")
    }


def test_confirmation_integre_la_commande_au_plan(entreprise, produits, en_tant_que):
    en_tant_que(Role.COMMERCIAL)
    assert not PlanProduction.objects.exists()
    commande = confirmee(entreprise, [(produits["baguette"], 120), (produits["croissant"], 30)])
    assert commande.statut == Statut.A_PLANIFIER
    plan = PlanProduction.objects.get()
    assert (plan.date_livraison, plan.statut) == (DEMAIN, StatutPlan.BROUILLON)
    assert besoins() == {"Pain baguette": (120, 120), "Croissant": (30, 30)}


def test_commandes_regroupees_par_produit_et_par_date(entreprise, produits, en_tant_que):
    en_tant_que(Role.COMMERCIAL)
    confirmee(entreprise, [(produits["baguette"], 120)])
    confirmee(entreprise, [(produits["baguette"], 80), (produits["croissant"], 40)])
    confirmee(entreprise, [(produits["baguette"], 500)], date=APRES_DEMAIN)
    nouvelle(entreprise, [(produits["baguette"], 999)])  # à confirmer : non comptée
    assert besoins() == {"Pain baguette": (200, 200), "Croissant": (40, 40)}
    assert besoins(APRES_DEMAIN) == {"Pain baguette": (500, 500)}


def test_marge_conservee_quand_le_commande_change(entreprise, produits, en_tant_que):
    en_tant_que(Role.COMMERCIAL)
    confirmee(entreprise, [(produits["baguette"], 1330)])
    besoin = BesoinProduit.objects.get()
    besoin.quantite_a_produire = 1400  # marge +70 fixée par le responsable
    besoin.save()
    confirmee(entreprise, [(produits["baguette"], 100)])
    besoin.refresh_from_db()
    assert (besoin.quantite_commandee, besoin.quantite_a_produire, besoin.marge) == (1430, 1500, 70)


def test_commande_confirmee_apres_validation_passe_en_preparation(
    entreprise, personnes, produits, en_tant_que
):
    en_tant_que(Role.COMMERCIAL)
    confirmee(entreprise, [(produits["baguette"], 100)])
    plan = PlanProduction.objects.get()
    plan.valider(par=personnes[Role.PRODUCTION])
    plan.save()
    tardive = confirmee(entreprise, [(produits["baguette"], 50)])
    assert tardive.statut == Statut.EN_PREPARATION
    assert besoins() == {"Pain baguette": (150, 150)}


def accepter_demande(personnes, demande):
    with activer_utilisateur(personnes[Role.DIRECTION]):
        commandes.decider_validation_commande(demande, accepter=True)


def test_modification_acceptee_recalcule_le_besoin(entreprise, personnes, produits, en_tant_que):
    en_tant_que(Role.COMMERCIAL)
    commande = confirmee(entreprise, [(produits["baguette"], 120), (produits["croissant"], 30)])
    demande = commandes.demander_modification(
        commande, "client", lignes=[(produits["baguette"], 100)]
    )
    accepter_demande(personnes, demande)
    assert besoins() == {"Pain baguette": (100, 100), "Croissant": (0, 0)}


def test_changement_de_date_recalcule_les_deux_plans(entreprise, personnes, produits, en_tant_que):
    en_tant_que(Role.COMMERCIAL)
    commande = confirmee(entreprise, [(produits["baguette"], 120)])
    demande = commandes.demander_modification(commande, "report", date_livraison=APRES_DEMAIN)
    accepter_demande(personnes, demande)
    assert besoins() == {"Pain baguette": (0, 0)}
    assert besoins(APRES_DEMAIN) == {"Pain baguette": (120, 120)}


def test_annulation_acceptee_retire_la_commande_du_plan(
    entreprise, personnes, produits, en_tant_que
):
    en_tant_que(Role.COMMERCIAL)
    gardee = confirmee(entreprise, [(produits["baguette"], 100)])
    annulee = confirmee(entreprise, [(produits["baguette"], 40)])
    accepter_demande(personnes, commandes.demander_annulation(annulee, "Fermeture"))
    assert besoins() == {"Pain baguette": (100, 100)}
    assert gardee.statut == Statut.A_PLANIFIER


def test_annulation_complete_si_la_cascade_echoue(entreprise, produits, en_tant_que):
    en_tant_que(Role.COMMERCIAL)
    commande = nouvelle(entreprise, [(produits["baguette"], 100)])
    with (
        mock.patch.object(services, "recalculer_besoin", side_effect=RuntimeError("panne")),
        pytest.raises(RuntimeError),
    ):
        commandes.confirmer_commande(commande)
    commande.refresh_from_db()
    assert commande.statut == Statut.A_CONFIRMER
    assert not PlanProduction.objects.exists()


def test_refus_de_l_urgence_n_integre_rien(entreprise, personnes, produits, en_tant_que):
    en_tant_que(Role.COMMERCIAL)
    adresse = AdresseFactory(client__entreprise=entreprise, par_defaut=True)
    commande = commandes.creer_commande(
        client=adresse.client,
        lignes=[(produits["baguette"], 10)],
        canal=Canal.TELEPHONE,
        date_livraison=DEMAIN,
        creneau="06h–08h",
        urgente=True,
    )
    commandes.soumettre_commande(commande)
    demande = Validation.objects.get(action=Validation.Action.URGENCE)
    with activer_utilisateur(personnes[Role.DIRECTION]):
        commandes.decider_validation_commande(demande, accepter=False)
    assert not PlanProduction.objects.exists()


@pytest.fixture
def groupes_des_roles():
    """Les tests transactionnels vident la base après eux, y compris les groupes créés
    par la migration `comptes.0002` : on les recrée pour ce test."""
    from django.contrib.auth.models import Group

    for role in Role:
        Group.objects.get_or_create(name=role.label)


@pytest.mark.django_db(transaction=True)
def test_confirmations_simultanees_sans_perte(groupes_des_roles, entreprise, produits):
    personnes = {
        Role.COMMERCIAL: UtilisateurFactory(entreprise=entreprise, roles=[Role.COMMERCIAL])
    }
    with activer_entreprise(entreprise), activer_utilisateur(personnes[Role.COMMERCIAL]):
        a_confirmer = [nouvelle(entreprise, [(produits["baguette"], 10)]) for _ in range(8)]

    def confirmer(commande):
        try:
            with activer_entreprise(entreprise), activer_utilisateur(personnes[Role.COMMERCIAL]):
                commandes.confirmer_commande(commande)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=8) as executeur:
        list(executeur.map(confirmer, a_confirmer))

    with activer_entreprise(entreprise):
        assert PlanProduction.objects.count() == 1
        assert BesoinProduit.objects.get().quantite_commandee == Decimal("80")


def test_isolation_entre_entreprises(entreprise, produits, en_tant_que):
    en_tant_que(Role.COMMERCIAL)
    confirmee(entreprise, [(produits["baguette"], 100)])
    autre = EntrepriseFactory()
    with activer_entreprise(autre):
        assert not PlanProduction.objects.exists()
        assert not BesoinProduit.objects.exists()


# --- Report de date (décision 8.2 du 20 octobre) -----------------------------------


def _valider(personnes, date=DEMAIN):
    plan = PlanProduction.objects.get(date_livraison=date)
    with activer_utilisateur(personnes[Role.PRODUCTION]):
        services.valider_plan(plan)


def test_report_vers_un_plan_non_valide_revient_a_planifier(
    entreprise, personnes, produits, en_tant_que
):
    en_tant_que(Role.COMMERCIAL)
    commande = confirmee(entreprise, [(produits["baguette"], 120)])
    _valider(personnes)
    commande.refresh_from_db()
    assert commande.statut == Statut.EN_PREPARATION
    demande = commandes.demander_modification(commande, "report", date_livraison=APRES_DEMAIN)
    accepter_demande(personnes, demande)
    commande.refresh_from_db()
    assert commande.statut == Statut.A_PLANIFIER


def test_report_vers_un_plan_valide_passe_en_preparation(
    entreprise, personnes, produits, en_tant_que
):
    en_tant_que(Role.COMMERCIAL)
    confirmee(entreprise, [(produits["croissant"], 10)], date=APRES_DEMAIN)
    _valider(personnes, APRES_DEMAIN)
    commande = confirmee(entreprise, [(produits["baguette"], 120)])
    assert commande.statut == Statut.A_PLANIFIER
    demande = commandes.demander_modification(commande, "report", date_livraison=APRES_DEMAIN)
    accepter_demande(personnes, demande)
    commande.refresh_from_db()
    assert commande.statut == Statut.EN_PREPARATION
