"""Une commande dans une tournée n'est ni annulée ni modifiée : la retirer d'abord (8.3)."""

import pytest
from django_fsm import can_proceed

from commandes import services as commandes
from commandes.models import Statut
from entreprises.tests.factories import EntrepriseFactory
from logistique import services
from logistique.tests.factories import TourneeFactory
from logistique.tests.test_services_controle import affecter, statut
from logistique.tests.test_services_tournee import DEMAIN
from noyau.contexte import activer_entreprise, activer_utilisateur
from noyau.models import Validation

pytestmark = pytest.mark.django_db


@pytest.fixture
def entreprise():
    entreprise = EntrepriseFactory()
    with activer_entreprise(entreprise):
        yield entreprise


@pytest.fixture
def chargement(entreprise, personnes):
    return affecter(TourneeFactory(entreprise=entreprise, date=DEMAIN), personnes)


def test_demande_d_annulation_refusee_dans_une_tournee(chargement, personnes):
    with activer_utilisateur(personnes.gestionnaire):
        with pytest.raises(commandes.ActionImpossible, match="la retirer"):
            commandes.demander_annulation(chargement.commande, "Client fermé")


def test_demande_de_modification_refusee_dans_une_tournee(chargement, personnes):
    with activer_utilisateur(personnes.gestionnaire):
        with pytest.raises(commandes.ActionImpossible, match="la retirer"):
            commandes.demander_modification(chargement.commande, "Autre créneau", creneau="08h–10h")


def test_annulation_impossible_meme_avec_validation(chargement):
    commande = chargement.commande
    commande.motif_annulation = "Client fermé"
    assert not can_proceed(commande.annuler)


def test_demande_anterieure_ne_s_applique_pas_puis_s_applique_apres_retrait(entreprise, personnes):
    """Demande faite avant l'affectation : acceptée seulement une fois la commande retirée."""
    tournee = TourneeFactory(entreprise=entreprise, date=DEMAIN)
    chargement = affecter(tournee, personnes)
    with activer_utilisateur(personnes.gestionnaire):
        services.retirer_commande(chargement)
        demande = commandes.demander_annulation(chargement.commande, "Client fermé")
        nouveau = services.affecter_commande(tournee, chargement.commande)
    with activer_utilisateur(personnes.direction):
        with pytest.raises(commandes.ActionImpossible, match="la retirer"):
            commandes.decider_validation_commande(demande, accepter=True)
    assert Validation.objects.get(pk=demande.pk).decision == Validation.Decision.EN_ATTENTE
    with activer_utilisateur(personnes.gestionnaire):
        services.retirer_commande(nouveau)
    with activer_utilisateur(personnes.direction):
        commandes.decider_validation_commande(demande, accepter=True)
    assert statut(chargement.commande) == Statut.ANNULEE


def test_refuser_une_demande_reste_possible(entreprise, personnes):
    tournee = TourneeFactory(entreprise=entreprise, date=DEMAIN)
    chargement = affecter(tournee, personnes)
    with activer_utilisateur(personnes.gestionnaire):
        services.retirer_commande(chargement)
        demande = commandes.demander_annulation(chargement.commande, "Client fermé")
        services.affecter_commande(tournee, chargement.commande)
    with activer_utilisateur(personnes.direction):
        commandes.decider_validation_commande(demande, accepter=False)
    assert statut(chargement.commande) == Statut.PRETE_CHARGEMENT
