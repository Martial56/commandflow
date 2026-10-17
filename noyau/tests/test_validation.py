import pytest
from django.db import transaction
from django_fsm import TransitionNotAllowed

from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from noyau.contexte import (
    AucunUtilisateurCourant,
    AutreEntreprise,
    activer_entreprise,
    activer_utilisateur,
)
from noyau.models import Validation
from noyau.services import decider_validation, demander_validation
from noyau.tests.models import ObjetTest

pytestmark = pytest.mark.django_db

Decision = Validation.Decision


@pytest.fixture
def contexte(utilisateur):
    """Entreprise et utilisateur courants, plus un objet métier de cette entreprise."""
    with activer_entreprise(utilisateur.entreprise), activer_utilisateur(utilisateur):
        yield ObjetTest.objects.create(nom="commande fictive")


@pytest.fixture
def responsable(utilisateur):
    return UtilisateurFactory(entreprise=utilisateur.entreprise)


def test_demande_creee_en_attente(contexte, utilisateur):
    validation = demander_validation(contexte, Validation.Action.ANNULATION, motif="client absent")
    assert validation.decision == Decision.EN_ATTENTE
    assert validation.objet == contexte
    assert validation.demandee_par == utilisateur
    assert validation.entreprise == utilisateur.entreprise


def test_demande_refusee_sur_un_objet_d_une_autre_entreprise(contexte):
    with activer_entreprise(None):
        autre = ObjetTest.toutes_entreprises.create(entreprise=EntrepriseFactory(), nom="autre")
    with pytest.raises(AutreEntreprise):
        demander_validation(autre, Validation.Action.URGENCE)


def test_demande_refusee_sans_utilisateur_courant(contexte):
    with activer_utilisateur(None), pytest.raises(AucunUtilisateurCourant):
        demander_validation(contexte, Validation.Action.URGENCE)


# Transitions : autorisées


def test_accepter(contexte, responsable):
    validation = demander_validation(contexte, Validation.Action.MODIFICATION)
    with activer_utilisateur(responsable):
        validation = decider_validation(validation, accepter=True)
    assert validation.decision == Decision.ACCEPTEE
    assert validation.validee_par == responsable
    assert validation.decidee_le is not None


def test_refuser(contexte, responsable):
    validation = demander_validation(contexte, Validation.Action.MODIFICATION)
    with activer_utilisateur(responsable):
        validation = decider_validation(validation, accepter=False)
    assert validation.decision == Decision.REFUSEE
    assert validation.validee_par == responsable


# Transitions : refusées


def test_decision_deja_prise_ne_peut_pas_changer(contexte):
    validation = decider_validation(
        demander_validation(contexte, Validation.Action.ECART), accepter=True
    )
    with pytest.raises(TransitionNotAllowed):
        decider_validation(validation, accepter=False)
    validation.refresh_from_db()
    assert validation.decision == Decision.ACCEPTEE


def test_decision_non_modifiable_directement(contexte):
    validation = demander_validation(contexte, Validation.Action.ECART)
    with pytest.raises(AttributeError):
        validation.decision = Decision.ACCEPTEE


def test_decision_annulee_avec_la_transaction_appelante(contexte):
    validation = demander_validation(contexte, Validation.Action.EXCEPTION)
    with pytest.raises(RuntimeError), transaction.atomic():
        decider_validation(validation, accepter=True)
        raise RuntimeError("erreur dans le service appelant")
    validation.refresh_from_db()
    assert validation.decision == Decision.EN_ATTENTE
    assert validation.validee_par is None


def test_isolation_des_validations(contexte):
    demander_validation(contexte, Validation.Action.URGENCE)
    with activer_entreprise(EntrepriseFactory()):
        assert Validation.objects.count() == 0
