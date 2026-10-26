"""Commandes prêtes au chargement (étape 6) : couverture, réservation, exception (8.2)."""

import datetime
from contextlib import ExitStack, contextmanager

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from catalogue.tests.factories import ProduitFactory
from clients.tests.factories import AdresseFactory
from commandes import services as commandes
from commandes.models import Canal, Commande, Statut
from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from noyau.contexte import activer_entreprise, activer_utilisateur
from noyau.models import Validation
from noyau.services import ValidationPropreDemande
from production import services
from production.models import BesoinProduit, PlanProduction

pytestmark = pytest.mark.django_db
DEMAIN = datetime.date.today() + datetime.timedelta(days=1)


@pytest.fixture
def entreprise():
    return EntrepriseFactory()


@pytest.fixture
def personnes(entreprise):
    return {role: UtilisateurFactory(entreprise=entreprise, roles=[role]) for role in Role}


@contextmanager
def en_tant_que(entreprise, personne):
    with ExitStack() as pile:
        pile.enter_context(activer_entreprise(entreprise))
        pile.enter_context(activer_utilisateur(personne))
        yield personne


@pytest.fixture
def baguette(entreprise):
    return ProduitFactory(entreprise=entreprise, nom="Pain baguette")


@pytest.fixture
def plan_du_jour(entreprise, personnes, baguette):
    """Trois commandes en préparation : 120 (06h), 100 (08h), 60 (06h)."""
    with en_tant_que(entreprise, personnes[Role.COMMERCIAL]):
        for quantite, creneau in [(120, "06h–08h"), (100, "08h–10h"), (60, "06h–08h")]:
            adresse = AdresseFactory(client__entreprise=entreprise, par_defaut=True)
            commande = commandes.creer_commande(
                client=adresse.client,
                lignes=[(baguette, quantite)],
                canal=Canal.TELEPHONE,
                date_livraison=DEMAIN,
                creneau=creneau,
            )
            commandes.soumettre_commande(commande)
            commandes.confirmer_commande(commande)
    plan = PlanProduction.toutes_entreprises.get(entreprise=entreprise)
    with en_tant_que(entreprise, personnes[Role.PRODUCTION]):
        services.valider_plan(plan)
    return plan


def produire(entreprise, personnes, quantite):
    besoin = BesoinProduit.toutes_entreprises.get(plan__entreprise=entreprise)
    with en_tant_que(entreprise, personnes[Role.PRODUCTION]):
        services.enregistrer_saisie(besoin, quantite)


def commandes_du_jour(entreprise):
    return list(Commande.toutes_entreprises.filter(entreprise=entreprise).order_by("pk"))


def besoin(entreprise):
    return BesoinProduit.toutes_entreprises.get(plan__entreprise=entreprise)


# --- une commande -----------------------------------------------------------------


@pytest.mark.parametrize("role", [Role.PRODUCTION, Role.GESTIONNAIRE])
def test_marquer_prete_reserve_les_quantites(entreprise, personnes, plan_du_jour, role):
    produire(entreprise, personnes, 200)
    premiere = commandes_du_jour(entreprise)[0]
    with en_tant_que(entreprise, personnes[role]):
        commandes.marquer_prete(premiere)
    premiere.refresh_from_db()
    assert premiere.statut == Statut.PRETE_CHARGEMENT
    b = besoin(entreprise)
    assert (b.quantite_produite, b.quantite_reservee, b.quantite_disponible) == (200, 120, 80)


def test_marquer_prete_refuse_si_le_disponible_ne_couvre_pas(entreprise, personnes, plan_du_jour):
    produire(entreprise, personnes, 200)
    premiere, deuxieme, _ = commandes_du_jour(entreprise)
    with en_tant_que(entreprise, personnes[Role.PRODUCTION]):
        commandes.marquer_prete(premiere)  # 200 − 120 = 80 disponibles
        with pytest.raises(commandes.ActionImpossible, match="exception"):
            commandes.marquer_prete(deuxieme)  # 100 demandés
    deuxieme.refresh_from_db()
    assert deuxieme.statut == Statut.EN_PREPARATION


@pytest.mark.parametrize("role", [Role.COMMERCIAL, Role.DIRECTION, Role.CHAUFFEUR])
def test_marquer_prete_refuse_aux_autres_roles(entreprise, personnes, plan_du_jour, role):
    produire(entreprise, personnes, 500)
    with en_tant_que(entreprise, personnes[role]), pytest.raises(PermissionDenied):
        commandes.marquer_prete(commandes_du_jour(entreprise)[0])


# --- tout le plan, dans l'ordre des créneaux ---------------------------------------


def test_marquer_tout_le_plan_dans_l_ordre_des_creneaux(entreprise, personnes, plan_du_jour):
    produire(entreprise, personnes, 200)
    a, b, c = commandes_du_jour(entreprise)  # 120 (06h), 100 (08h), 60 (06h)
    with en_tant_que(entreprise, personnes[Role.GESTIONNAIRE]):
        resultat = services.marquer_plan_pret(plan_du_jour)
    # 06h–08h d'abord : 120 puis 60 (180 ≤ 200) ; la commande de 08h–10h reste.
    assert {x.pk for x in resultat.pretes} == {a.pk, c.pk}
    assert [x.pk for x in resultat.restantes] == [b.pk]
    assert besoin(entreprise).quantite_reservee == 180


def test_marquer_tout_le_plan_refuse_aux_autres_roles(entreprise, personnes, plan_du_jour):
    with en_tant_que(entreprise, personnes[Role.DIRECTION]), pytest.raises(PermissionDenied):
        services.marquer_plan_pret(plan_du_jour)


# --- exception ---------------------------------------------------------------------


def test_exception_acceptee_par_un_autre_responsable(entreprise, personnes, plan_du_jour):
    produire(entreprise, personnes, 50)
    premiere = commandes_du_jour(entreprise)[0]
    with en_tant_que(entreprise, personnes[Role.PRODUCTION]):
        demande = commandes.demander_exception(premiere, "Client accepte une livraison partielle")
    with en_tant_que(entreprise, personnes[Role.DIRECTION]):
        commandes.decider_validation_commande(demande, accepter=True)
    premiere.refresh_from_db()
    assert premiere.statut == Statut.PRETE_CHARGEMENT
    assert besoin(entreprise).quantite_reservee == 120


def test_exception_refusee(entreprise, personnes, plan_du_jour):
    premiere = commandes_du_jour(entreprise)[0]
    with en_tant_que(entreprise, personnes[Role.PRODUCTION]):
        demande = commandes.demander_exception(premiere, "urgence")
    with en_tant_que(entreprise, personnes[Role.DIRECTION]):
        commandes.decider_validation_commande(demande, accepter=False)
    premiere.refresh_from_db()
    assert premiere.statut == Statut.EN_PREPARATION


def test_exception_pas_d_auto_validation(entreprise, personnes, plan_du_jour):
    premiere = commandes_du_jour(entreprise)[0]
    with en_tant_que(entreprise, personnes[Role.GESTIONNAIRE]):
        demande = commandes.demander_exception(premiere, "urgence")
        with pytest.raises(ValidationPropreDemande):
            commandes.decider_validation_commande(demande, accepter=True)


def test_exception_exige_un_motif_et_le_bon_statut(entreprise, personnes, plan_du_jour):
    premiere = commandes_du_jour(entreprise)[0]
    with en_tant_que(entreprise, personnes[Role.PRODUCTION]):
        with pytest.raises(ValidationError):
            commandes.demander_exception(premiere, " ")
        commandes.demander_exception(premiere, "urgence")
        with pytest.raises(commandes.ActionImpossible):
            commandes.demander_exception(premiere, "encore")  # déjà en attente
    assert Validation.toutes_entreprises.count() == 1


def test_exception_refusee_aux_autres_roles(entreprise, personnes, plan_du_jour):
    with en_tant_que(entreprise, personnes[Role.COMMERCIAL]), pytest.raises(PermissionDenied):
        commandes.demander_exception(commandes_du_jour(entreprise)[0], "urgence")


# --- cascade : l'annulation d'une commande prête libère sa réservation ---------------


def test_annulation_d_une_commande_prete_libere_la_reservation(entreprise, personnes, plan_du_jour):
    produire(entreprise, personnes, 300)
    premiere = commandes_du_jour(entreprise)[0]
    with en_tant_que(entreprise, personnes[Role.PRODUCTION]):
        commandes.marquer_prete(premiere)
    assert besoin(entreprise).quantite_reservee == 120
    with en_tant_que(entreprise, personnes[Role.COMMERCIAL]):
        demande = commandes.demander_annulation(premiere, "Fermeture")
    with en_tant_que(entreprise, personnes[Role.DIRECTION]):
        commandes.decider_validation_commande(demande, accepter=True)
    b = besoin(entreprise)
    assert (b.quantite_commandee, b.quantite_reservee) == (160, 0)
