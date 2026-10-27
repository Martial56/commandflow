"""Transitions du Sprint 4 (chargement, tournée, commande « En livraison ») : un cas
autorisé et des cas refusés pour chacune (mauvais statut, mauvais rôle, condition)."""

from decimal import Decimal

import pytest
from django_fsm import TransitionNotAllowed, can_proceed, has_transition_perm

from commandes.models import Commande, Statut
from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from logistique.models import (
    Chargement,
    MotifEcart,
    StatutChargement,
    StatutTournee,
    Tournee,
)
from logistique.tests.factories import (
    ChargementFactory,
    LigneChargementFactory,
    PrestataireFactory,
)
from noyau.contexte import activer_entreprise, activer_utilisateur
from noyau.models import Validation
from noyau.services import decider_validation, demander_validation

pytestmark = pytest.mark.django_db
R = Role


@pytest.fixture
def chargement():
    """Chargement d'une ligne attendue à 100, dans le contexte de son entreprise."""
    ligne = LigneChargementFactory(quantite_attendue=100)
    with activer_entreprise(ligne.entreprise):
        yield ligne.chargement


def forcer(objet, statut):
    """Place un objet dans un statut (préparation des tests, sans transition)."""
    type(objet).toutes_entreprises.filter(pk=objet.pk).update(statut=statut)
    objet.refresh_from_db()
    return objet


def utilisateur(objet, *roles):
    return UtilisateurFactory(entreprise=objet.entreprise, roles=list(roles))


def compter(chargement, prepare=None, recu=None):
    for ligne in chargement.lignes.all():
        ligne.quantite_preparee = prepare
        ligne.quantite_recue_chauffeur = recu
        ligne.save()


def accepter_exception(chargement, objet):
    """Exception demandée par un préparateur, acceptée par un gestionnaire."""
    with activer_utilisateur(utilisateur(chargement, R.PREPARATION)):
        validation = demander_validation(
            chargement, Validation.Action.EXCEPTION, motif="test", contenu={"objet": objet}
        )
    with activer_utilisateur(utilisateur(chargement, R.GESTIONNAIRE)):
        decider_validation(validation, accepter=True)


# --- Contrôle du préparateur ---------------------------------------------------


@pytest.mark.parametrize("role", [R.PREPARATION, R.GESTIONNAIRE])
def test_commencer_verification_autorise(chargement, role):
    assert has_transition_perm(chargement.commencer_verification, utilisateur(chargement, role))
    chargement.commencer_verification()
    assert chargement.statut == StatutChargement.EN_VERIFICATION


@pytest.mark.parametrize("role", [R.CHAUFFEUR, R.COMMERCIAL, R.PRODUCTION, R.DIRECTION])
def test_commencer_verification_refuse_au_mauvais_role(chargement, role):
    assert not has_transition_perm(chargement.commencer_verification, utilisateur(chargement, role))


def test_commencer_verification_refuse_a_une_autre_entreprise(chargement):
    with activer_entreprise(None):
        etranger = UtilisateurFactory(entreprise=EntrepriseFactory(), roles=[R.PREPARATION])
    assert not has_transition_perm(chargement.commencer_verification, etranger)


def test_valider_preparation_si_tout_est_conforme(chargement):
    preparateur = utilisateur(chargement, R.PREPARATION)
    forcer(chargement, StatutChargement.EN_VERIFICATION)
    assert not can_proceed(chargement.valider_preparation)  # rien compté
    compter(chargement, prepare=Decimal(100))
    assert has_transition_perm(chargement.valider_preparation, preparateur)
    chargement.valider_preparation(par=preparateur)
    assert chargement.statut == StatutChargement.VERIFIEE_PREPARATION
    assert chargement.verifie_par == preparateur and chargement.verifie_le


def test_valider_preparation_refusee_en_cas_d_ecart(chargement):
    forcer(chargement, StatutChargement.EN_VERIFICATION)
    compter(chargement, prepare=Decimal(80))
    assert not can_proceed(chargement.valider_preparation)


def test_valider_preparation_refusee_au_mauvais_statut(chargement):
    compter(chargement, prepare=Decimal(100))
    with pytest.raises(TransitionNotAllowed):
        chargement.valider_preparation(par=None)


def test_constater_ecart_exige_un_motif(chargement):
    forcer(chargement, StatutChargement.EN_VERIFICATION)
    compter(chargement, prepare=Decimal(80))
    assert not can_proceed(chargement.constater_ecart)
    ligne = chargement.lignes.get()
    ligne.motif_ecart = MotifEcart.PRODUCTION_INSUFFISANTE
    ligne.save()
    chargement.constater_ecart(par=utilisateur(chargement, R.PREPARATION))
    assert chargement.statut == StatutChargement.ECART_CONSTATE


def test_constater_ecart_refuse_sans_ecart(chargement):
    forcer(chargement, StatutChargement.EN_VERIFICATION)
    compter(chargement, prepare=Decimal(100))
    assert not can_proceed(chargement.constater_ecart)


def test_completer_relance_le_controle(chargement):
    forcer(chargement, StatutChargement.ECART_CONSTATE)
    assert has_transition_perm(chargement.completer, utilisateur(chargement, R.PREPARATION))
    assert not has_transition_perm(chargement.completer, utilisateur(chargement, R.CHAUFFEUR))
    chargement.completer()
    assert chargement.statut == StatutChargement.EN_VERIFICATION


def test_accepter_exception_exige_une_validation_acceptee(chargement):
    forcer(chargement, StatutChargement.ECART_CONSTATE)
    assert not can_proceed(chargement.accepter_exception)
    accepter_exception(chargement, "depart")  # pas le bon objet
    assert not can_proceed(chargement.accepter_exception)
    accepter_exception(chargement, "ecart")
    chargement.accepter_exception()
    assert chargement.statut == StatutChargement.VERIFIEE_PREPARATION


# --- Réception du livreur -------------------------------------------------------


def test_le_chauffeur_affecte_confirme_la_reception(chargement):
    forcer(chargement, StatutChargement.VERIFIEE_PREPARATION)
    compter(chargement, prepare=Decimal(100), recu=Decimal(100))
    chauffeur = chargement.tournee.chauffeur
    assert has_transition_perm(chargement.confirmer_reception, chauffeur)
    chargement.confirmer_reception(par=chauffeur)
    assert chargement.statut == StatutChargement.RECUE_CHAUFFEUR
    assert chargement.recu_par == chauffeur and chargement.recu_le


@pytest.mark.parametrize("role", [R.PREPARATION, R.GESTIONNAIRE, R.DIRECTION, R.CHAUFFEUR])
def test_personne_d_autre_ne_confirme_la_reception(chargement, role):
    """Le préparateur ne peut jamais marquer « Reçue par le chauffeur » (§10)."""
    forcer(chargement, StatutChargement.VERIFIEE_PREPARATION)
    compter(chargement, prepare=Decimal(100), recu=Decimal(100))
    assert not has_transition_perm(chargement.confirmer_reception, utilisateur(chargement, role))


def test_reception_refusee_si_quantite_differente_ou_non_saisie(chargement):
    forcer(chargement, StatutChargement.VERIFIEE_PREPARATION)
    compter(chargement, prepare=Decimal(100))
    assert not can_proceed(chargement.confirmer_reception)
    compter(chargement, prepare=Decimal(100), recu=Decimal(90))
    assert not can_proceed(chargement.confirmer_reception)


def test_reception_refusee_au_mauvais_statut(chargement):
    forcer(chargement, StatutChargement.EN_VERIFICATION)
    compter(chargement, prepare=Decimal(100), recu=Decimal(100))
    assert not can_proceed(chargement.confirmer_reception)


def test_prestataire_avec_compte_confirme_lui_meme(chargement):
    tournee = chargement.tournee
    compte = utilisateur(chargement, R.PRESTATAIRE)
    tournee.chauffeur = None
    tournee.prestataire = PrestataireFactory(entreprise=tournee.entreprise, compte=compte)
    tournee.save()
    forcer(chargement, StatutChargement.VERIFIEE_PREPARATION)
    compter(chargement, prepare=Decimal(100), recu=Decimal(100))
    assert has_transition_perm(chargement.confirmer_reception, compte)
    assert not has_transition_perm(
        chargement.confirmer_reception, utilisateur(chargement, R.GESTIONNAIRE)
    )


def test_prestataire_sans_compte_le_gestionnaire_saisit(chargement):
    tournee = chargement.tournee
    tournee.chauffeur = None
    tournee.prestataire = PrestataireFactory(entreprise=tournee.entreprise)
    tournee.save()
    forcer(chargement, StatutChargement.VERIFIEE_PREPARATION)
    compter(chargement, prepare=Decimal(100), recu=Decimal(100))
    assert has_transition_perm(
        chargement.confirmer_reception, utilisateur(chargement, R.GESTIONNAIRE)
    )
    for role in (R.PREPARATION, R.DIRECTION):
        assert not has_transition_perm(
            chargement.confirmer_reception, utilisateur(chargement, role)
        )


def test_contester_exige_un_motif_et_un_ecart(chargement):
    forcer(chargement, StatutChargement.VERIFIEE_PREPARATION)
    compter(chargement, prepare=Decimal(100), recu=Decimal(90))
    assert not can_proceed(chargement.contester)
    chargement.motif_contestation = "10 pains manquants"
    assert has_transition_perm(chargement.contester, chargement.tournee.chauffeur)
    assert not has_transition_perm(chargement.contester, utilisateur(chargement, R.PREPARATION))
    chargement.contester()
    assert chargement.statut == StatutChargement.ECART_CONSTATE


def test_contester_refuse_si_tout_est_recu(chargement):
    forcer(chargement, StatutChargement.VERIFIEE_PREPARATION)
    compter(chargement, prepare=Decimal(100), recu=Decimal(100))
    chargement.motif_contestation = "rien"
    assert not can_proceed(chargement.contester)


# --- Retrait --------------------------------------------------------------------


@pytest.mark.parametrize("role", [R.GESTIONNAIRE, R.DIRECTION])
def test_retirer_avant_le_depart(chargement, role):
    forcer(chargement, StatutChargement.RECUE_CHAUFFEUR)
    assert has_transition_perm(chargement.retirer, utilisateur(chargement, role))
    chargement.retirer()
    assert chargement.statut == StatutChargement.RETIRE


@pytest.mark.parametrize("role", [R.PREPARATION, R.CHAUFFEUR, R.COMMERCIAL])
def test_retirer_refuse_au_mauvais_role(chargement, role):
    assert not has_transition_perm(chargement.retirer, utilisateur(chargement, role))


def test_retirer_refuse_apres_le_depart(chargement):
    forcer(chargement.tournee, StatutTournee.EN_COURS)
    assert not can_proceed(chargement.retirer)
    assert not can_proceed(chargement.commencer_verification)


def test_retirer_refuse_si_deja_retire(chargement):
    forcer(chargement, StatutChargement.RETIRE)
    with pytest.raises(TransitionNotAllowed):
        chargement.retirer()


# --- Tournée --------------------------------------------------------------------


def test_commencer_chargement(chargement):
    tournee = chargement.tournee
    tournee.commencer_chargement()
    assert tournee.statut == StatutTournee.EN_CHARGEMENT
    with pytest.raises(TransitionNotAllowed):
        tournee.commencer_chargement()


def test_autoriser_depart_quand_tout_est_recu(chargement):
    tournee = forcer(chargement.tournee, StatutTournee.EN_CHARGEMENT)
    assert not can_proceed(tournee.autoriser_depart)
    forcer(chargement, StatutChargement.RECUE_CHAUFFEUR)
    tournee.autoriser_depart()
    assert tournee.statut == StatutTournee.DEPART_AUTORISE


def test_autoriser_depart_par_exception(chargement):
    tournee = forcer(chargement.tournee, StatutTournee.EN_CHARGEMENT)
    forcer(chargement, StatutChargement.ECART_CONSTATE)
    accepter_exception(chargement, "depart")
    assert can_proceed(tournee.autoriser_depart)


def test_autoriser_depart_ignore_les_chargements_retires(chargement):
    tournee = forcer(chargement.tournee, StatutTournee.EN_CHARGEMENT)
    forcer(chargement, StatutChargement.RECUE_CHAUFFEUR)
    autre = forcer(ChargementFactory(tournee=tournee), StatutChargement.A_PREPARER)
    assert not can_proceed(tournee.autoriser_depart)
    forcer(autre, StatutChargement.RETIRE)
    assert can_proceed(tournee.autoriser_depart)


def test_autoriser_depart_refuse_sans_chargement():
    chargement = ChargementFactory()
    with activer_entreprise(chargement.entreprise):
        forcer(chargement, StatutChargement.RETIRE)
        tournee = forcer(chargement.tournee, StatutTournee.EN_CHARGEMENT)
        assert not can_proceed(tournee.autoriser_depart)


def test_rouvrir_chargement(chargement):
    tournee = forcer(chargement.tournee, StatutTournee.DEPART_AUTORISE)
    tournee.rouvrir_chargement()
    assert tournee.statut == StatutTournee.EN_CHARGEMENT


def test_le_livreur_demarre_la_tournee(chargement):
    tournee = forcer(chargement.tournee, StatutTournee.DEPART_AUTORISE)
    forcer(chargement, StatutChargement.RECUE_CHAUFFEUR)
    assert has_transition_perm(tournee.demarrer, tournee.chauffeur)
    for role in (R.PREPARATION, R.GESTIONNAIRE, R.CHAUFFEUR):
        assert not has_transition_perm(tournee.demarrer, utilisateur(tournee, role))
    tournee.demarrer()
    assert tournee.statut == StatutTournee.EN_COURS and tournee.depart_le


def test_demarrer_refuse_avant_l_autorisation(chargement):
    tournee = forcer(chargement.tournee, StatutTournee.EN_CHARGEMENT)
    forcer(chargement, StatutChargement.RECUE_CHAUFFEUR)
    with pytest.raises(TransitionNotAllowed):
        tournee.demarrer()


def test_terminer(chargement):
    tournee = forcer(chargement.tournee, StatutTournee.EN_COURS)
    tournee.terminer()
    assert tournee.statut == StatutTournee.TERMINEE and tournee.fin_le
    tournee = forcer(Tournee.objects.get(pk=tournee.pk), StatutTournee.DEPART_AUTORISE)
    with pytest.raises(TransitionNotAllowed):
        tournee.terminer()


# --- Commande « En livraison » -------------------------------------------------------


def test_commande_part_en_livraison_si_chargement_recu(chargement):
    commande = forcer(chargement.commande, Statut.PRETE_CHARGEMENT)
    assert not can_proceed(commande.partir_en_livraison)
    forcer(chargement, StatutChargement.RECUE_CHAUFFEUR)
    commande.partir_en_livraison()
    assert commande.statut == Statut.EN_LIVRAISON


def test_commande_part_par_exception_de_depart(chargement):
    commande = forcer(chargement.commande, Statut.PRETE_CHARGEMENT)
    forcer(chargement, StatutChargement.ECART_CONSTATE)
    accepter_exception(chargement, "depart")
    assert can_proceed(commande.partir_en_livraison)


def test_commande_ne_part_pas_si_chargement_retire(chargement):
    commande = forcer(chargement.commande, Statut.PRETE_CHARGEMENT)
    forcer(chargement, StatutChargement.RETIRE)
    assert not can_proceed(commande.partir_en_livraison)


def test_commande_ne_part_pas_au_mauvais_statut(chargement):
    commande = forcer(chargement.commande, Statut.EN_PREPARATION)
    forcer(chargement, StatutChargement.RECUE_CHAUFFEUR)
    assert isinstance(commande, Commande)
    with pytest.raises(TransitionNotAllowed):
        commande.partir_en_livraison()
    assert Chargement.objects.get(pk=chargement.pk).statut == StatutChargement.RECUE_CHAUFFEUR
