"""Transitions du Sprint 5 (livraison, fin de tournée, commande après livraison) : un cas
autorisé et des cas refusés pour chacune (mauvais statut, mauvais rôle, condition)."""

import datetime
from decimal import Decimal

import pytest
from django_fsm import TransitionNotAllowed, can_proceed, has_transition_perm

from commandes.models import Statut
from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from logistique.models import (
    AccordClient,
    MotifEchec,
    MoyenAccord,
    ObjetAccord,
    Preuve,
    Reliquat,
    StatutLivraison,
    StatutTournee,
    TypePreuve,
)
from logistique.tests.factories import LigneLivraisonFactory, PrestataireFactory
from logistique.tests.test_transitions import forcer, utilisateur
from noyau.contexte import activer_entreprise, activer_utilisateur
from noyau.models import Validation
from noyau.services import decider_validation, demander_validation

pytestmark = pytest.mark.django_db
R = Role
DEMAIN = datetime.date.today() + datetime.timedelta(days=1)


@pytest.fixture
def livraison():
    """Livraison d'une ligne prévue à 100, tournée en cours, commande en livraison."""
    ligne = LigneLivraisonFactory(quantite_prevue=100)
    livraison = ligne.livraison
    with activer_entreprise(ligne.entreprise):
        forcer(livraison.tournee, StatutTournee.EN_COURS)
        forcer(livraison.commande, Statut.EN_LIVRAISON)
        yield livraison


@pytest.fixture
def chauffeur(livraison):
    return livraison.tournee.chauffeur


def sur_place(livraison):
    return forcer(livraison, StatutLivraison.SUR_PLACE)


def saisir(livraison, livre):
    for ligne in livraison.lignes.all():
        ligne.quantite_livree = livre
        ligne.save()


def regler_preuves(livraison, reglage):
    entreprise = livraison.entreprise
    entreprise.options = {"preuves": {livraison.commande.client.categorie: reglage}}
    entreprise.save()
    livraison.commande.refresh_from_db()


def accord(livraison):
    AccordClient.objects.create(
        livraison=livraison,
        objet=ObjetAccord.PARTIELLE,
        nom_client="M. Kouassi",
        moyen=MoyenAccord.ORAL,
    )


# --- Arrivée -------------------------------------------------------------------------


def test_arriver_autorise_au_livreur(livraison, chauffeur):
    assert has_transition_perm(livraison.arriver, chauffeur)
    livraison.arriver()
    assert livraison.statut == StatutLivraison.SUR_PLACE and livraison.arrivee_le


@pytest.mark.parametrize("role", [R.CHAUFFEUR, R.GESTIONNAIRE, R.DIRECTION, R.PREPARATION])
def test_arriver_refuse_a_un_autre(livraison, role):
    assert not has_transition_perm(livraison.arriver, utilisateur(livraison, role))


def test_arriver_refuse_a_une_autre_entreprise(livraison, chauffeur):
    with activer_entreprise(None):
        etranger = UtilisateurFactory(entreprise=EntrepriseFactory(), roles=[R.CHAUFFEUR])
    assert not has_transition_perm(livraison.arriver, etranger)


def test_arriver_refuse_si_la_tournee_n_est_pas_partie(livraison):
    forcer(livraison.tournee, StatutTournee.DEPART_AUTORISE)
    assert not can_proceed(livraison.arriver)


def test_arriver_refuse_au_mauvais_statut(livraison):
    sur_place(livraison)
    with pytest.raises(TransitionNotAllowed):
        livraison.arriver()


def test_arriver_exige_la_position_si_l_option_l_impose(livraison):
    regler_preuves(livraison, ["gps"])
    assert not can_proceed(livraison.arriver)
    livraison.latitude, livraison.longitude = Decimal("5.35"), Decimal("-4.00")
    assert can_proceed(livraison.arriver)


def test_prestataire_sans_compte_le_gestionnaire_livre_a_sa_place(livraison):
    tournee = livraison.tournee
    tournee.chauffeur, tournee.prestataire = None, PrestataireFactory(entreprise=tournee.entreprise)
    tournee.save()
    assert has_transition_perm(livraison.arriver, utilisateur(livraison, R.GESTIONNAIRE))
    assert not has_transition_perm(livraison.arriver, utilisateur(livraison, R.DIRECTION))


def test_prestataire_avec_compte_livre_lui_meme(livraison):
    compte = utilisateur(livraison, R.PRESTATAIRE)
    tournee = livraison.tournee
    tournee.chauffeur = None
    tournee.prestataire = PrestataireFactory(entreprise=tournee.entreprise, compte=compte)
    tournee.save()
    assert has_transition_perm(livraison.arriver, compte)
    assert not has_transition_perm(livraison.arriver, utilisateur(livraison, R.GESTIONNAIRE))


# --- Livrée --------------------------------------------------------------------------


def test_livrer_avec_quantites_completes_et_receptionnaire(livraison, chauffeur):
    sur_place(livraison)
    saisir(livraison, Decimal(100))
    assert not can_proceed(livraison.livrer)  # aucune preuve
    livraison.receptionnaire_nom = "Mme Bamba"
    assert has_transition_perm(livraison.livrer, chauffeur)
    livraison.livrer()
    assert livraison.statut == StatutLivraison.LIVREE and livraison.traitee_le


def test_livrer_avec_la_photo_du_bordereau(livraison):
    """Décision 8.4 : photo du bordereau ou nom du réceptionnaire."""
    sur_place(livraison)
    saisir(livraison, Decimal(100))
    Preuve.objects.create(livraison=livraison, type=TypePreuve.PHOTO_BORDEREAU)
    assert can_proceed(livraison.livrer)


def test_livrer_refuse_si_une_quantite_manque(livraison):
    sur_place(livraison)
    livraison.receptionnaire_nom = "Mme Bamba"
    assert not can_proceed(livraison.livrer)  # rien saisi
    saisir(livraison, Decimal(90))
    assert not can_proceed(livraison.livrer)


def test_livrer_selon_les_preuves_de_la_categorie(livraison):
    regler_preuves(livraison, ["photo_bordereau", "receptionnaire"])  # les deux exigées
    sur_place(livraison)
    saisir(livraison, Decimal(100))
    livraison.receptionnaire_nom = "Mme Bamba"
    assert not can_proceed(livraison.livrer)
    Preuve.objects.create(livraison=livraison, type=TypePreuve.PHOTO_BORDEREAU)
    assert can_proceed(livraison.livrer)


def test_livrer_refuse_a_un_autre_chauffeur(livraison):
    sur_place(livraison)
    assert not has_transition_perm(livraison.livrer, utilisateur(livraison, R.CHAUFFEUR))


def test_livrer_refuse_au_mauvais_statut(livraison):
    saisir(livraison, Decimal(100))
    livraison.receptionnaire_nom = "Mme Bamba"
    with pytest.raises(TransitionNotAllowed):
        livraison.livrer()


# --- Partielle -----------------------------------------------------------------------


def preparer_partielle(livraison):
    sur_place(livraison)
    saisir(livraison, Decimal(70))
    livraison.receptionnaire_nom = "Mme Bamba"
    livraison.motif_echec = MotifEchec.MARCHANDISE_MANQUANTE


def test_livrer_partiellement_avec_accord_et_motif(livraison, chauffeur):
    preparer_partielle(livraison)
    assert not can_proceed(livraison.livrer_partiellement)  # pas d'accord client
    accord(livraison)
    assert has_transition_perm(livraison.livrer_partiellement, chauffeur)
    livraison.livrer_partiellement()
    assert livraison.statut == StatutLivraison.PARTIELLEMENT_LIVREE


def test_livrer_partiellement_exige_un_motif(livraison):
    preparer_partielle(livraison)
    accord(livraison)
    livraison.motif_echec = ""
    assert not can_proceed(livraison.livrer_partiellement)


def test_livrer_partiellement_exige_une_preuve(livraison):
    preparer_partielle(livraison)
    accord(livraison)
    livraison.receptionnaire_nom = ""
    assert not can_proceed(livraison.livrer_partiellement)


@pytest.mark.parametrize("livre", [Decimal(0), Decimal(100)])
def test_livrer_partiellement_refuse_si_rien_ou_tout_est_livre(livraison, livre):
    preparer_partielle(livraison)
    accord(livraison)
    saisir(livraison, livre)
    assert not can_proceed(livraison.livrer_partiellement)


# --- Échec et report -----------------------------------------------------------------


@pytest.mark.parametrize("depart", [StatutLivraison.EN_ATTENTE, StatutLivraison.SUR_PLACE])
def test_echouer_avec_un_motif(livraison, chauffeur, depart):
    forcer(livraison, depart)
    assert not can_proceed(livraison.echouer)
    livraison.motif_echec = MotifEchec.CLIENT_ABSENT
    assert has_transition_perm(livraison.echouer, chauffeur)
    livraison.echouer()
    assert livraison.statut == StatutLivraison.ECHOUEE


def test_echouer_refuse_apres_livraison(livraison):
    forcer(livraison, StatutLivraison.LIVREE)
    livraison.motif_echec = MotifEchec.REFUS
    with pytest.raises(TransitionNotAllowed):
        livraison.echouer()


def test_reporter_par_le_gestionnaire(livraison):
    forcer(livraison, StatutLivraison.ECHOUEE)
    gestionnaire = utilisateur(livraison, R.GESTIONNAIRE)
    assert not can_proceed(livraison.reporter)  # pas de date
    livraison.reportee_au = DEMAIN
    assert has_transition_perm(livraison.reporter, gestionnaire)
    livraison.reporter()
    assert livraison.statut == StatutLivraison.REPORTEE


@pytest.mark.parametrize("role", [R.CHAUFFEUR, R.DIRECTION, R.COMMERCIAL])
def test_reporter_refuse_aux_autres_roles(livraison, chauffeur, role):
    """Décision 8.4 : le chauffeur ne reporte pas lui-même."""
    forcer(livraison, StatutLivraison.ECHOUEE)
    livraison.reportee_au = DEMAIN
    assert not has_transition_perm(livraison.reporter, chauffeur)
    assert not has_transition_perm(livraison.reporter, utilisateur(livraison, role))


def test_reporter_refuse_au_mauvais_statut(livraison):
    livraison.reportee_au = DEMAIN
    with pytest.raises(TransitionNotAllowed):
        livraison.reporter()


# --- Fin de tournée ------------------------------------------------------------------


def test_terminer_quand_tout_est_traite(livraison):
    tournee = livraison.tournee
    assert not can_proceed(tournee.terminer)  # livraison en attente
    sur_place(livraison)
    assert not can_proceed(tournee.terminer)
    forcer(livraison, StatutLivraison.ECHOUEE)
    tournee.terminer()
    assert tournee.statut == StatutTournee.TERMINEE and tournee.fin_le


def test_terminer_refuse_au_mauvais_statut(livraison):
    forcer(livraison, StatutLivraison.LIVREE)
    tournee = forcer(livraison.tournee, StatutTournee.DEPART_AUTORISE)
    with pytest.raises(TransitionNotAllowed):
        tournee.terminer()


# --- Commande après la livraison (§4) ------------------------------------------------


def reliquat(livraison, quantite=30, traite=False):
    return Reliquat.objects.create(
        commande_origine=livraison.commande,
        livraison=livraison,
        ligne_commande=livraison.lignes.get().ligne_commande,
        quantite=quantite,
        traite=traite,
    )


def test_cloturer_livree(livraison):
    commande = livraison.commande
    assert not can_proceed(commande.cloturer_livree)
    forcer(livraison, StatutLivraison.LIVREE)
    commande.cloturer_livree()
    assert commande.statut == Statut.LIVREE


def test_cloturer_partielle_exige_le_reliquat(livraison):
    commande = livraison.commande
    forcer(livraison, StatutLivraison.PARTIELLEMENT_LIVREE)
    assert not can_proceed(commande.cloturer_partielle)
    assert not can_proceed(commande.cloturer_livree)
    reliquat(livraison)
    commande.cloturer_partielle()
    assert commande.statut == Statut.PARTIELLEMENT_LIVREE


def test_cloturer_reliquat_quand_il_est_traite(livraison):
    commande = forcer(livraison.commande, Statut.PARTIELLEMENT_LIVREE)
    ouvert = reliquat(livraison)
    assert not can_proceed(commande.cloturer_reliquat)
    ouvert.traite = True
    ouvert.save()
    commande.cloturer_reliquat()
    assert commande.statut == Statut.LIVREE


def test_cloturer_reliquat_refuse_au_mauvais_statut(livraison):
    with pytest.raises(TransitionNotAllowed):
        livraison.commande.cloturer_reliquat()


def test_signaler_anomalie(livraison):
    commande = livraison.commande
    assert not can_proceed(commande.signaler_anomalie)
    forcer(livraison, StatutLivraison.ECHOUEE)
    commande.signaler_anomalie()
    assert commande.statut == Statut.EN_ANOMALIE


def test_signaler_anomalie_refuse_au_mauvais_statut(livraison):
    forcer(livraison, StatutLivraison.ECHOUEE)
    commande = forcer(livraison.commande, Statut.LIVREE)
    with pytest.raises(TransitionNotAllowed):
        commande.signaler_anomalie()


def test_relancer_par_le_gestionnaire_apres_report(livraison):
    commande = forcer(livraison.commande, Statut.EN_ANOMALIE)
    gestionnaire = utilisateur(livraison, R.GESTIONNAIRE)
    forcer(livraison, StatutLivraison.ECHOUEE)
    assert not can_proceed(commande.relancer)
    forcer(livraison, StatutLivraison.REPORTEE)
    assert has_transition_perm(commande.relancer, gestionnaire)
    commande.relancer()
    assert commande.statut == Statut.A_PLANIFIER


@pytest.mark.parametrize("role", [R.COMMERCIAL, R.DIRECTION, R.CHAUFFEUR])
def test_relancer_refuse_aux_autres_roles(livraison, role):
    commande = forcer(livraison.commande, Statut.EN_ANOMALIE)
    forcer(livraison, StatutLivraison.REPORTEE)
    assert not has_transition_perm(commande.relancer, utilisateur(livraison, role))


def valider_abandon(commande):
    with activer_utilisateur(utilisateur(commande, R.GESTIONNAIRE)):
        demande = demander_validation(commande, Validation.Action.ANNULATION, motif="Client parti")
    with activer_utilisateur(utilisateur(commande, R.DIRECTION)):
        decider_validation(demande, accepter=True)


def test_abandonner_avec_motif_et_validation(livraison):
    commande = forcer(livraison.commande, Statut.EN_ANOMALIE)
    commande.motif_annulation = "Client parti"
    assert not can_proceed(commande.abandonner)  # pas de validation
    valider_abandon(commande)
    assert has_transition_perm(commande.abandonner, utilisateur(commande, R.DIRECTION))
    commande.abandonner()
    assert commande.statut == Statut.ANNULEE


def test_abandonner_refuse_sans_motif(livraison):
    commande = forcer(livraison.commande, Statut.EN_ANOMALIE)
    valider_abandon(commande)
    assert not can_proceed(commande.abandonner)


@pytest.mark.parametrize("role", [R.COMMERCIAL, R.CHAUFFEUR])
def test_abandonner_refuse_aux_autres_roles(livraison, role):
    commande = forcer(livraison.commande, Statut.EN_ANOMALIE)
    assert not has_transition_perm(commande.abandonner, utilisateur(commande, role))
