"""Écran Tournées (maquette 14) : accès, affichage, actions et rafraîchissement htmx."""

import pytest
from django.urls import reverse

from commandes.models import Statut
from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from logistique import services
from logistique.etat import exceptions
from logistique.models import StatutChargement, Tournee
from logistique.tests.factories import PrestataireFactory, TourneeFactory, VehiculeFactory
from logistique.tests.test_services_controle import affecter, compter, statut
from logistique.tests.test_services_tournee import DEMAIN, commande_prete
from noyau.contexte import activer_entreprise, activer_utilisateur
from noyau.models import Validation

pytestmark = pytest.mark.django_db
R = Role
ECRAN = reverse("ecran_tournees")
DATE = {"date": DEMAIN.isoformat()}


@pytest.fixture(autouse=True)
def sans_2fa_obligatoire(settings):
    settings.MIDDLEWARE = [
        m for m in settings.MIDDLEWARE if "DoubleAuthentificationObligatoire" not in m
    ]


@pytest.fixture
def entreprise():
    entreprise = EntrepriseFactory()
    with activer_entreprise(entreprise):
        yield entreprise


@pytest.fixture
def tournee(entreprise, personnes):
    return TourneeFactory(entreprise=entreprise, date=DEMAIN)


def connecter(client, personne):
    client.force_login(personne)
    return client


# --- Accès ------------------------------------------------------------------------------


@pytest.mark.parametrize("role", [R.GESTIONNAIRE, R.DIRECTION])
def test_ecran_ouvert_aux_responsables(client, entreprise, role):
    connecter(client, UtilisateurFactory(entreprise=entreprise, roles=[role]))
    reponse = client.get(ECRAN, DATE)
    assert reponse.status_code == 200
    assert "Tournées ·" in reponse.content.decode()


@pytest.mark.parametrize("role", [R.PREPARATION, R.CHAUFFEUR, R.COMMERCIAL, R.PRODUCTION])
def test_ecran_ferme_aux_autres_roles(client, entreprise, role):
    connecter(client, UtilisateurFactory(entreprise=entreprise, roles=[role]))
    assert client.get(ECRAN).status_code == 403


def test_actions_refusees_aux_autres_roles(client, entreprise, personnes, tournee):
    chargement = affecter(tournee, personnes)
    connecter(client, personnes.preparateur)
    assert client.post(reverse("chargement_retirer", args=[chargement.pk])).status_code == 403
    assert client.post(reverse("tournee_creer"), DATE).status_code == 403
    assert statut(chargement) == StatutChargement.A_PREPARER


# --- Affichage ---------------------------------------------------------------------------


def test_affichage_a_affecter_et_tournee(client, entreprise, personnes, tournee):
    commande = commande_prete(entreprise, commune="Marcory")
    chargement = affecter(tournee, personnes)
    compter(chargement, [30, 20], personnes.preparateur, motif="production_insuffisante")
    texte = connecter(client, personnes.gestionnaire).get(ECRAN, DATE).content.decode()
    assert "À affecter" in texte and commande.client.nom in texte
    assert "Marcory · 06h–08h · 80 pièces" in texte
    assert tournee.numero in texte and "Cocody" in texte
    assert "0/1" in texte  # vérifiées et reçues
    assert "écart -30" in texte and "production insuffisante" in texte


def test_rafraichissement_htmx_renvoie_le_fragment(client, personnes, tournee):
    reponse = connecter(client, personnes.gestionnaire).get(
        ECRAN, DATE, headers={"HX-Request": "true"}
    )
    texte = reponse.content.decode()
    assert tournee.numero in texte and "<nav" not in texte
    assert "every 30s" in connecter(client, personnes.gestionnaire).get(ECRAN).content.decode()


def test_isolation_entre_entreprises(client, personnes, tournee):
    with activer_entreprise(None):
        autre = EntrepriseFactory()
        TourneeFactory(entreprise=autre, date=DEMAIN, numero="TRN-2026-999999")
        etranger = UtilisateurFactory(entreprise=autre, roles=[R.GESTIONNAIRE])
    texte = connecter(client, etranger).get(ECRAN, DATE).content.decode()
    assert "TRN-2026-999999" in texte and tournee.numero not in texte


# --- Actions -----------------------------------------------------------------------------


def test_creer_une_tournee(client, entreprise, personnes):
    chauffeur = UtilisateurFactory(entreprise=entreprise, roles=[R.CHAUFFEUR])
    vehicule = VehiculeFactory(entreprise=entreprise)
    reponse = connecter(client, personnes.gestionnaire).post(
        reverse("tournee_creer"),
        {**DATE, "livreur": f"c-{chauffeur.pk}", "vehicule": vehicule.pk},
    )
    assert reponse.status_code == 302 and reponse.url == f"{ECRAN}?date={DEMAIN.isoformat()}"
    tournee = Tournee.objects.get()
    assert (tournee.chauffeur, tournee.vehicule, tournee.date) == (chauffeur, vehicule, DEMAIN)


def test_creer_sans_livreur_affiche_l_erreur(client, personnes):
    reponse = connecter(client, personnes.gestionnaire).post(
        reverse("tournee_creer"), DATE, follow=True
    )
    assert "Choisir un chauffeur ou un prestataire." in reponse.content.decode()
    assert not Tournee.objects.exists()


def test_affecter_retirer_et_reordonner(client, entreprise, personnes, tournee):
    connecter(client, personnes.gestionnaire)
    premiere, seconde = commande_prete(entreprise), commande_prete(entreprise)
    for commande in (premiere, seconde):
        client.post(reverse("tournee_affecter"), {"tournee": tournee.pk, "commande": commande.pk})
    premier, second = tournee.chargements_actifs().order_by("ordre_passage")
    assert (premier.commande, second.commande) == (premiere, seconde)
    client.post(reverse("chargement_deplacer", args=[second.pk]), {"sens": "haut"})
    assert list(tournee.chargements_actifs()) == [second, premier]
    client.post(reverse("chargement_retirer", args=[second.pk]))
    assert statut(second) == StatutChargement.RETIRE
    assert statut(premier.commande) == Statut.PRETE_CHARGEMENT


def test_changer_de_livreur(client, entreprise, personnes, tournee):
    prestataire = PrestataireFactory(entreprise=entreprise)
    connecter(client, personnes.gestionnaire).post(
        reverse("tournee_livreur", args=[tournee.pk]), {"livreur": f"p-{prestataire.pk}"}
    )
    tournee.refresh_from_db()
    assert tournee.prestataire == prestataire and tournee.chauffeur is None


def test_exception_de_depart_puis_decision(client, personnes, tournee):
    chargement = affecter(tournee, personnes)
    connecter(client, personnes.gestionnaire).post(
        reverse("chargement_exception_depart", args=[chargement.pk]), {"motif": "Client prévenu"}
    )
    demande = exceptions(chargement, "depart", Validation.Decision.EN_ATTENTE).get()
    # Le demandeur ne peut pas décider : pas de bouton, refus côté serveur.
    texte = client.get(ECRAN, DATE).content.decode()
    assert "En attente d'un autre responsable." in texte
    reponse = client.post(
        reverse("chargement_decider", args=[demande.pk]), {"decision": "accepter"}, follow=True
    )
    assert "propre demande" in reponse.content.decode()
    connecter(client, personnes.direction).post(
        reverse("chargement_decider", args=[demande.pk]), {"decision": "accepter"}
    )
    demande.refresh_from_db()
    assert demande.decision == Validation.Decision.ACCEPTEE


def test_traiter_un_ecart(client, personnes, tournee):
    chargement = affecter(tournee, personnes)
    compter(chargement, [30, 20], personnes.preparateur, motif="produit_abime")
    with activer_utilisateur(personnes.preparateur):
        services.signaler_ecart(chargement)
    demande = exceptions(chargement, "ecart", Validation.Decision.EN_ATTENTE).get()
    texte = connecter(client, personnes.gestionnaire).get(ECRAN, DATE).content.decode()
    assert "Écart signalé par la préparation" in texte and "Accepter" in texte
    client.post(reverse("chargement_decider", args=[demande.pk]), {"decision": "refuser"})
    demande.refresh_from_db()
    assert demande.decision == Validation.Decision.REFUSEE
    assert statut(chargement) == StatutChargement.ECART_CONSTATE
