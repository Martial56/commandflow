"""Fiche commande et production (étape 9) : frise, historique, préparation, suivi."""

import datetime
import html
import re
from contextlib import ExitStack, contextmanager

import pytest
from django.urls import reverse

from catalogue.tests.factories import ProduitFactory
from clients.tests.factories import AdresseFactory
from commandes import services
from commandes.models import Canal, Commande, Statut
from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from noyau.contexte import activer_entreprise, activer_utilisateur
from production import services as production
from production.models import BesoinProduit, PlanProduction

pytestmark = pytest.mark.django_db
DEMAIN = datetime.date.today() + datetime.timedelta(days=1)


@pytest.fixture(autouse=True)
def sans_2fa_obligatoire(settings):
    settings.MIDDLEWARE = [
        m for m in settings.MIDDLEWARE if "DoubleAuthentificationObligatoire" not in m
    ]


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
def commande(entreprise, personnes):
    """Commande confirmée (donc planifiée), plan validé : « En préparation »."""
    baguette = ProduitFactory(entreprise=entreprise, nom="Pain baguette")
    with en_tant_que(entreprise, personnes[Role.COMMERCIAL]):
        adresse = AdresseFactory(client__entreprise=entreprise, par_defaut=True)
        nouvelle = services.creer_commande(
            client=adresse.client,
            lignes=[(baguette, 120)],
            canal=Canal.TELEPHONE,
            date_livraison=DEMAIN,
            creneau="06h–08h",
        )
        services.soumettre_commande(nouvelle)
        services.confirmer_commande(nouvelle)
    plan = PlanProduction.toutes_entreprises.get(entreprise=entreprise)
    with en_tant_que(entreprise, personnes[Role.PRODUCTION]):
        production.valider_plan(plan)
    return Commande.toutes_entreprises.get(pk=nouvelle.pk)


def produire(entreprise, personnes, quantite):
    besoin = BesoinProduit.toutes_entreprises.get(plan__entreprise=entreprise)
    with en_tant_que(entreprise, personnes[Role.PRODUCTION]):
        production.enregistrer_saisie(besoin, quantite)


def fiche(client, personne, commande):
    client.force_login(personne)
    reponse = client.get(reverse("commande_detail", args=[commande.pk]))
    contenu = html.unescape(reponse.content.decode())
    return contenu, " ".join(re.sub(r"<[^>]+>", " ", contenu).split())


def test_frise_et_historique_de_production(client, personnes, commande):
    contenu, lisible = fiche(client, personnes[Role.GESTIONNAIRE], commande)
    for etape in ("Confirmée", "Planifiée", "En préparation"):
        assert f'<strong class="font-semibold">{etape}</strong>' in contenu
    assert '<strong class="font-semibold">Prête à charger</strong>' not in contenu
    assert "Intégrée au plan de production du" in lisible
    assert "Plan de production validé : passage en préparation" in lisible
    assert "Production Plan du" in lisible and "· validé" in lisible


def test_marquer_prete_depuis_la_fiche(client, entreprise, personnes, commande):
    produire(entreprise, personnes, 200)
    contenu, lisible = fiche(client, personnes[Role.GESTIONNAIRE], commande)
    assert "Les quantités produites couvrent cette commande." in lisible
    reponse = client.post(reverse("commande_marquer_prete", args=[commande.pk]), follow=True)
    lisible = " ".join(re.sub(r"<[^>]+>", " ", html.unescape(reponse.content.decode())).split())
    assert "Commande prête au chargement : quantités réservées." in lisible
    assert "Prête au chargement : quantités réservées" in lisible  # historique
    commande.refresh_from_db()
    assert commande.statut == Statut.PRETE_CHARGEMENT


def test_exception_depuis_la_fiche_puis_decision(client, entreprise, personnes, commande):
    _, lisible = fiche(client, personnes[Role.GESTIONNAIRE], commande)
    assert "Quantités disponibles insuffisantes" in lisible
    client.post(
        reverse("commande_demander_exception", args=[commande.pk]),
        {"motif": "Client d'accord pour une livraison partielle"},
    )
    contenu, lisible = fiche(client, personnes[Role.DIRECTION], commande)
    assert "Exception demandée par" in lisible
    demande = re.search(r"/demandes/(\d+)/decider/", contenu).group(1)
    client.post(reverse("commande_decider", args=[commande.pk, demande]), {"decision": "accepter"})
    commande.refresh_from_db()
    assert commande.statut == Statut.PRETE_CHARGEMENT


@pytest.mark.parametrize("role", [Role.COMMERCIAL, Role.DIRECTION, Role.FACTURATION])
def test_preparation_masquee_et_refusee_aux_autres_roles(
    client, entreprise, personnes, commande, role
):
    produire(entreprise, personnes, 200)
    _, lisible = fiche(client, personnes[role], commande)
    assert "Marquer prête au chargement" not in lisible
    reponse = client.post(reverse("commande_marquer_prete", args=[commande.pk]))
    assert reponse.status_code == 403
    commande.refresh_from_db()
    assert commande.statut == Statut.EN_PREPARATION


def test_retour_a_planifier_dans_l_historique(client, entreprise, personnes, commande):
    with en_tant_que(entreprise, personnes[Role.COMMERCIAL]):
        demande = services.demander_modification(
            commande, "report", date_livraison=DEMAIN + datetime.timedelta(days=2)
        )
    with en_tant_que(entreprise, personnes[Role.DIRECTION]):
        services.decider_validation_commande(demande, accepter=True)
    _, lisible = fiche(client, personnes[Role.GESTIONNAIRE], commande)
    assert "Reportée vers un plan non validé : retour « À planifier »" in lisible
    assert "Pas encore planifiée" not in lisible  # plan de la nouvelle date créé
