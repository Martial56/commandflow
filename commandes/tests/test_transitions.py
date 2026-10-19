"""Transitions du Sprint 2 : un cas autorisé et des cas refusés pour chacune
(mauvais statut, mauvais rôle, condition manquante)."""

import pytest
from django_fsm import TransitionNotAllowed, can_proceed, has_transition_perm

from commandes.models import HistoriqueCommande, Statut
from commandes.tests.factories import CommandeFactory
from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from noyau.contexte import activer_entreprise, activer_utilisateur
from noyau.models import Validation
from noyau.services import decider_validation, demander_validation

pytestmark = pytest.mark.django_db
R = Role


def passer(commande, *transitions):
    """Amène la commande à un statut (préparation des tests, sans contrôle de rôle)."""
    for nom, kwargs in transitions:
        getattr(commande, nom)(**kwargs)
        commande.save()
    return commande


@pytest.fixture
def contexte():
    """Entreprise courante de la commande créée par `commande`."""
    pile = []

    def activer(commande, utilisateur=None):
        for gestionnaire in (
            activer_entreprise(commande.entreprise),
            activer_utilisateur(utilisateur),
        ):
            gestionnaire.__enter__()
            pile.append(gestionnaire)

    yield activer
    for gestionnaire in reversed(pile):
        gestionnaire.__exit__(None, None, None)


def utilisateur(commande, *roles):
    return UtilisateurFactory(entreprise=commande.entreprise, roles=list(roles))


def accepter(commande, action, responsable):
    with activer_utilisateur(responsable):
        validation = demander_validation(commande, action, motif="test")
        decider_validation(validation, accepter=True)


# --- soumettre ---------------------------------------------------------------


@pytest.mark.parametrize("role", [R.COMMERCIAL, R.GESTIONNAIRE])
def test_soumettre_autorise(contexte, role):
    commande = CommandeFactory()
    contexte(commande)
    assert has_transition_perm(commande.soumettre, utilisateur(commande, role))
    passer(commande, ("soumettre", {}))
    assert commande.statut == Statut.A_CONFIRMER


@pytest.mark.parametrize("role", [R.FACTURATION, R.CHAUFFEUR, R.PRODUCTION, R.DIRECTION])
def test_soumettre_refuse_au_mauvais_role(contexte, role):
    commande = CommandeFactory()
    contexte(commande)
    assert not has_transition_perm(commande.soumettre, utilisateur(commande, role))


def test_soumettre_refuse_a_une_autre_entreprise(contexte):
    commande = CommandeFactory()
    contexte(commande)
    with activer_entreprise(None):
        etranger = UtilisateurFactory(entreprise=EntrepriseFactory(), roles=[R.GESTIONNAIRE])
    assert not has_transition_perm(commande.soumettre, etranger)


def test_soumettre_refuse_sans_ligne(contexte):
    commande = CommandeFactory(avec_ligne=False)
    contexte(commande)
    assert not can_proceed(commande.soumettre)
    with pytest.raises(TransitionNotAllowed):
        commande.soumettre()


def test_soumettre_refuse_au_mauvais_statut(contexte):
    commande = CommandeFactory()
    contexte(commande)
    passer(commande, ("soumettre", {}))
    with pytest.raises(TransitionNotAllowed):
        commande.soumettre()


# --- confirmer ---------------------------------------------------------------


@pytest.mark.parametrize("role", [R.COMMERCIAL, R.GESTIONNAIRE])
def test_confirmer_autorise(contexte, role):
    commande = CommandeFactory()
    contexte(commande)
    passer(commande, ("soumettre", {}))
    assert has_transition_perm(commande.confirmer, utilisateur(commande, role))
    passer(commande, ("confirmer", {"note": "confirmé par WhatsApp"}))
    commande.refresh_from_db()
    assert (commande.statut, commande.note_confirmation) == (
        Statut.CONFIRMEE, "confirmé par WhatsApp",
    )  # fmt: skip


@pytest.mark.parametrize("role", [R.FACTURATION, R.COMPTABILITE, R.CHAUFFEUR])
def test_confirmer_refuse_au_mauvais_role(contexte, role):
    commande = CommandeFactory()
    contexte(commande)
    passer(commande, ("soumettre", {}))
    assert not has_transition_perm(commande.confirmer, utilisateur(commande, role))


def test_confirmer_refuse_au_mauvais_statut(contexte):
    commande = CommandeFactory()
    contexte(commande)
    with pytest.raises(TransitionNotAllowed):
        commande.confirmer()


def test_urgente_confirmee_seulement_apres_validation(contexte):
    commande = CommandeFactory(urgente=True)
    responsable = utilisateur(commande, R.GESTIONNAIRE)
    contexte(commande, responsable)
    passer(commande, ("soumettre", {}))
    assert not can_proceed(commande.confirmer)

    with activer_utilisateur(responsable):
        validation = demander_validation(commande, Validation.Action.URGENCE)
        decider_validation(validation, accepter=False)
    assert not can_proceed(commande.confirmer)

    accepter(commande, Validation.Action.URGENCE, responsable)
    passer(commande, ("confirmer", {}))
    assert commande.statut == Statut.CONFIRMEE


# --- annuler -----------------------------------------------------------------


def peut_annuler(utilisateur, commande):
    """Permission seule (rôle), indépendamment des conditions de la transition."""
    return utilisateur.has_perm("commandes.annuler_commande", commande)


def test_annuler_un_brouillon_avec_motif_par_son_createur(contexte):
    createur = UtilisateurFactory(roles=[R.COMMERCIAL])
    with activer_entreprise(createur.entreprise), activer_utilisateur(createur):
        commande = CommandeFactory(adresse__client__entreprise=createur.entreprise)
    contexte(commande)
    assert commande.cree_par == createur
    assert peut_annuler(createur, commande)
    assert peut_annuler(utilisateur(commande, R.GESTIONNAIRE), commande)
    assert not peut_annuler(utilisateur(commande, R.COMMERCIAL), commande)  # pas le créateur

    assert not can_proceed(commande.annuler)  # motif manquant
    commande.motif_annulation = "Client a renoncé"
    assert has_transition_perm(commande.annuler, createur)
    passer(commande, ("annuler", {}))
    assert commande.statut == Statut.ANNULEE


def test_annuler_apres_confirmation_exige_validation_et_responsable(contexte):
    commande = CommandeFactory()
    gestionnaire = utilisateur(commande, R.GESTIONNAIRE)
    contexte(commande, gestionnaire)
    passer(commande, ("soumettre", {}), ("confirmer", {}))
    commande.motif_annulation = "Fermeture exceptionnelle"
    direction, commercial = utilisateur(commande, R.DIRECTION), utilisateur(commande, R.COMMERCIAL)

    assert peut_annuler(direction, commande) and peut_annuler(gestionnaire, commande)
    assert not peut_annuler(commercial, commande)

    assert not can_proceed(commande.annuler)  # validation manquante
    accepter(commande, Validation.Action.ANNULATION, gestionnaire)
    assert has_transition_perm(commande.annuler, direction)
    assert not has_transition_perm(commande.annuler, commercial)
    passer(commande, ("annuler", {}))
    assert commande.statut == Statut.ANNULEE


def test_annuler_refuse_une_commande_deja_annulee(contexte):
    commande = CommandeFactory(motif_annulation="Doublon")
    contexte(commande)
    passer(commande, ("annuler", {}))
    with pytest.raises(TransitionNotAllowed):
        commande.annuler()


@pytest.mark.parametrize("statut", [Statut.EN_LIVRAISON, Statut.LIVREE, Statut.EN_ANOMALIE])
def test_aucune_annulation_apres_depart(contexte, statut):
    commande = CommandeFactory(statut=statut, motif_annulation="Trop tard")
    contexte(commande)
    assert not can_proceed(commande.annuler)


def test_historique_des_transitions(contexte):
    commande = CommandeFactory()
    gestionnaire = utilisateur(commande, R.GESTIONNAIRE)
    contexte(commande, gestionnaire)
    passer(commande, ("soumettre", {}), ("confirmer", {}))
    statuts = list(
        HistoriqueCommande.objects.filter(pgh_obj=commande)
        .order_by("pgh_id")
        .values_list("statut", "modifie_par")
    )
    assert statuts == [
        (Statut.BROUILLON, None),
        (Statut.A_CONFIRMER, gestionnaire.pk),
        (Statut.CONFIRMEE, gestionnaire.pk),
    ]
