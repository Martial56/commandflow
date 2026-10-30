"""Services de contrôle et de réception du chargement (§10, décisions 8.3).

Recette : §22-17 (le préparateur coche, puis le chauffeur confirme lui-même) et §22-18
(heure, identités et écarts conservés ; pas de départ sans confirmation ni exception).
"""

from decimal import Decimal

import pytest
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django_fsm import can_proceed

from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from logistique import services
from logistique.etat import exceptions, recapitulatif
from logistique.models import (
    Chargement,
    HistoriqueLigneChargement,
    MotifEcart,
    StatutChargement,
    StatutTournee,
)
from logistique.services import ActionImpossible
from logistique.tests.factories import PrestataireFactory, TourneeFactory
from logistique.tests.test_services_tournee import DEMAIN, commande_prete
from noyau.contexte import activer_entreprise, activer_utilisateur
from noyau.models import Validation
from noyau.services import ValidationPropreDemande

pytestmark = pytest.mark.django_db
R = Role
EN_ATTENTE = Validation.Decision.EN_ATTENTE


@pytest.fixture
def entreprise():
    entreprise = EntrepriseFactory()
    with activer_entreprise(entreprise):
        yield entreprise


@pytest.fixture
def personnes(entreprise):
    def creer(role):
        return UtilisateurFactory(entreprise=entreprise, roles=[role])

    class Personnes:
        gestionnaire = creer(R.GESTIONNAIRE)
        direction = creer(R.DIRECTION)
        preparateur = creer(R.PREPARATION)
        autre_chauffeur = creer(R.CHAUFFEUR)

    return Personnes


@pytest.fixture
def tournee(entreprise, personnes):
    return TourneeFactory(entreprise=entreprise, date=DEMAIN)


def affecter(tournee, personnes):
    """Commande de deux lignes (60 et 20) affectée à la tournée."""
    with activer_utilisateur(personnes.gestionnaire):
        return services.affecter_commande(tournee, commande_prete(tournee.entreprise))


def lignes(chargement):
    return list(chargement.lignes.order_by("pk"))


def compter(chargement, quantites, personne, motif=""):
    with activer_utilisateur(personne):
        for ligne, quantite in zip(lignes(chargement), quantites, strict=True):
            services.compter_ligne(ligne, quantite, motif=motif)


def recues(chargement, quantites):
    return dict(zip(lignes(chargement), quantites, strict=True))


def verifier(chargement, personnes):
    compter(chargement, [60, 20], personnes.preparateur)
    with activer_utilisateur(personnes.preparateur):
        return services.valider_preparation(chargement)


def statut(objet):
    objet.refresh_from_db()
    return objet.statut


# --- Comptage et validation du préparateur -----------------------------------------


def test_premier_comptage_lance_le_controle(tournee, personnes):
    chargement = affecter(tournee, personnes)
    compter(chargement, [60, 20], personnes.preparateur)
    assert statut(chargement) == StatutChargement.EN_VERIFICATION
    assert statut(tournee) == StatutTournee.EN_CHARGEMENT
    assert [ligne.quantite_preparee for ligne in lignes(chargement)] == [60, 20]


@pytest.mark.parametrize("role", [R.CHAUFFEUR, R.COMMERCIAL, R.DIRECTION])
def test_comptage_refuse_au_mauvais_role(entreprise, tournee, personnes, role):
    chargement = affecter(tournee, personnes)
    with pytest.raises(PermissionDenied):
        compter(chargement, [60, 20], UtilisateurFactory(entreprise=entreprise, roles=[role]))
    assert statut(chargement) == StatutChargement.A_PREPARER


@pytest.mark.parametrize("quantite", [-1, "abc", None])
def test_comptage_refuse_une_quantite_invalide(tournee, personnes, quantite):
    chargement = affecter(tournee, personnes)
    with activer_utilisateur(personnes.preparateur), pytest.raises(ActionImpossible):
        services.compter_ligne(lignes(chargement)[0], quantite)


def test_comptage_refuse_un_motif_inconnu(tournee, personnes):
    chargement = affecter(tournee, personnes)
    with activer_utilisateur(personnes.preparateur), pytest.raises(ActionImpossible):
        services.compter_ligne(lignes(chargement)[0], 30, motif="vol")


def test_ligne_conforme_perd_son_motif(tournee, personnes):
    chargement = affecter(tournee, personnes)
    ligne = lignes(chargement)[0]
    with activer_utilisateur(personnes.preparateur):
        services.compter_ligne(ligne, 30, motif=MotifEcart.PRODUIT_ABIME)
        ligne = services.compter_ligne(ligne, 60, motif=MotifEcart.PRODUIT_ABIME)
    assert ligne.motif_ecart == ""


def test_valider_preparation(tournee, personnes):
    chargement = verifier(affecter(tournee, personnes), personnes)
    assert chargement.statut == StatutChargement.VERIFIEE_PREPARATION
    assert chargement.verifie_par == personnes.preparateur


def test_valider_preparation_refusee_si_un_produit_n_est_pas_compte(tournee, personnes):
    chargement = affecter(tournee, personnes)
    with activer_utilisateur(personnes.preparateur):
        services.compter_ligne(lignes(chargement)[0], 60)
        with pytest.raises(ActionImpossible, match="Compter chaque produit"):
            services.valider_preparation(chargement)


def test_le_preparateur_ne_peut_jamais_confirmer_la_reception(tournee, personnes):
    """§10 : la confirmation du chauffeur est requise."""
    chargement = verifier(affecter(tournee, personnes), personnes)
    with activer_utilisateur(personnes.preparateur), pytest.raises(PermissionDenied):
        services.confirmer_reception(chargement, recues(chargement, [60, 20]))
    assert statut(chargement) == StatutChargement.VERIFIEE_PREPARATION
    assert all(ligne.quantite_recue_chauffeur is None for ligne in lignes(chargement))


# --- Écart du préparateur -------------------------------------------------------------


def test_signaler_un_ecart_demande_une_decision(tournee, personnes):
    chargement = affecter(tournee, personnes)
    compter(chargement, [30, 20], personnes.preparateur, motif=MotifEcart.PRODUCTION_INSUFFISANTE)
    with activer_utilisateur(personnes.preparateur):
        services.signaler_ecart(chargement, "30 croissants manquants")
    assert statut(chargement) == StatutChargement.ECART_CONSTATE
    demande = exceptions(chargement, "ecart", EN_ATTENTE).get()
    assert demande.contenu["origine"] == "preparation"
    assert sorted(demande.contenu["lignes"].values()) == ["20.000", "30.000"]
    assert recapitulatif(tournee) == {"attendues": 1, "verifiees": 0, "ecarts": 1, "recues": 0}


def test_signaler_un_ecart_exige_un_motif(tournee, personnes):
    chargement = affecter(tournee, personnes)
    compter(chargement, [30, 20], personnes.preparateur)
    with activer_utilisateur(personnes.preparateur), pytest.raises(ActionImpossible):
        services.signaler_ecart(chargement)


def test_motif_autre_exige_une_precision(tournee, personnes):
    chargement = affecter(tournee, personnes)
    compter(chargement, [30, 20], personnes.preparateur, motif=MotifEcart.AUTRE)
    with activer_utilisateur(personnes.preparateur):
        with pytest.raises(ActionImpossible, match="Préciser"):
            services.signaler_ecart(chargement)
        services.compter_ligne(lignes(chargement)[0], 30, MotifEcart.AUTRE, "tombé du camion")
        services.signaler_ecart(chargement)
    assert statut(chargement) == StatutChargement.ECART_CONSTATE


def test_signaler_annule_tout_si_la_demande_echoue(tournee, personnes, monkeypatch):
    chargement = affecter(tournee, personnes)
    compter(chargement, [30, 20], personnes.preparateur, motif=MotifEcart.PRODUIT_ABIME)

    def echec(*args, **kwargs):
        raise RuntimeError("panne")

    monkeypatch.setattr(services, "demander_validation", echec)
    with activer_utilisateur(personnes.preparateur), pytest.raises(RuntimeError):
        services.signaler_ecart(chargement)
    assert statut(chargement) == StatutChargement.EN_VERIFICATION


def _ecart_signale(tournee, personnes):
    chargement = affecter(tournee, personnes)
    compter(chargement, [30, 20], personnes.preparateur, motif=MotifEcart.PRODUCTION_INSUFFISANTE)
    with activer_utilisateur(personnes.preparateur):
        services.signaler_ecart(chargement)
    return chargement, exceptions(chargement, "ecart", EN_ATTENTE).get()


def test_ecart_accepte_par_un_responsable(tournee, personnes):
    """Décision 8.3 : la commande ne change pas, les quantités chargées font foi."""
    chargement, demande = _ecart_signale(tournee, personnes)
    with activer_utilisateur(personnes.gestionnaire):
        services.decider_exception_chargement(demande, accepter=True)
    assert statut(chargement) == StatutChargement.VERIFIEE_PREPARATION
    with activer_utilisateur(tournee.chauffeur):
        services.confirmer_reception(chargement, recues(chargement, [30, 20]))
    assert statut(chargement) == StatutChargement.RECUE_CHAUFFEUR
    assert [ligne.quantite_attendue for ligne in lignes(chargement)] == [60, 20]


def test_ecart_refuse_puis_marchandise_completee(tournee, personnes):
    chargement, demande = _ecart_signale(tournee, personnes)
    with activer_utilisateur(personnes.direction):
        services.decider_exception_chargement(demande, accepter=False)
    assert statut(chargement) == StatutChargement.ECART_CONSTATE
    with activer_utilisateur(personnes.preparateur):
        services.completer_chargement(chargement)
    verifier(chargement, personnes)
    assert statut(chargement) == StatutChargement.VERIFIEE_PREPARATION


@pytest.mark.parametrize("role", [R.PREPARATION, R.CHAUFFEUR, R.COMMERCIAL])
def test_decision_refusee_au_mauvais_role(entreprise, tournee, personnes, role):
    chargement, demande = _ecart_signale(tournee, personnes)
    with activer_utilisateur(UtilisateurFactory(entreprise=entreprise, roles=[role])):
        with pytest.raises(PermissionDenied):
            services.decider_exception_chargement(demande, accepter=True)
    assert statut(chargement) == StatutChargement.ECART_CONSTATE


def test_demande_depassee_ne_s_applique_pas(tournee, personnes):
    """Recompté et resignalé : seule la dernière demande peut être acceptée."""
    chargement, ancienne = _ecart_signale(tournee, personnes)
    with activer_utilisateur(personnes.preparateur):
        services.completer_chargement(chargement)
    compter(chargement, [50, 20], personnes.preparateur, motif=MotifEcart.PRODUIT_ABIME)
    with activer_utilisateur(personnes.preparateur):
        with pytest.raises(ActionImpossible, match="déjà en attente"):
            services.signaler_ecart(chargement)
    with activer_utilisateur(personnes.gestionnaire):
        with pytest.raises(ActionImpossible, match="dépassée"):
            services.decider_exception_chargement(ancienne, accepter=True)
        services.decider_exception_chargement(ancienne, accepter=False)
    with activer_utilisateur(personnes.preparateur):
        services.signaler_ecart(chargement)
    nouvelle = exceptions(chargement, "ecart", EN_ATTENTE).get()
    with activer_utilisateur(personnes.gestionnaire):
        services.decider_exception_chargement(nouvelle, accepter=True)
    assert chargement.quantites_retenues() == dict(
        zip([ligne.pk for ligne in lignes(chargement)], [Decimal(50), Decimal(20)], strict=True)
    )


# --- Réception du livreur -------------------------------------------------------------


def test_reception_par_le_chauffeur_autorise_le_depart(tournee, personnes):
    chargement = verifier(affecter(tournee, personnes), personnes)
    with activer_utilisateur(tournee.chauffeur):
        services.confirmer_reception(chargement, recues(chargement, [60, 20]))
    assert statut(chargement) == StatutChargement.RECUE_CHAUFFEUR
    assert statut(tournee) == StatutTournee.DEPART_AUTORISE


@pytest.mark.parametrize("personne", ["gestionnaire", "direction", "autre_chauffeur"])
def test_reception_refusee_a_un_autre_que_le_livreur(tournee, personnes, personne):
    chargement = verifier(affecter(tournee, personnes), personnes)
    with activer_utilisateur(getattr(personnes, personne)), pytest.raises(PermissionDenied):
        services.confirmer_reception(chargement, recues(chargement, [60, 20]))


def test_reception_refusee_si_quantite_differente(tournee, personnes):
    chargement = verifier(affecter(tournee, personnes), personnes)
    with activer_utilisateur(tournee.chauffeur):
        with pytest.raises(ActionImpossible, match="signaler un écart"):
            services.confirmer_reception(chargement, recues(chargement, [55, 20]))
        with pytest.raises(ActionImpossible, match="chaque produit"):
            services.confirmer_reception(chargement, {lignes(chargement)[0]: 60})
    assert statut(chargement) == StatutChargement.VERIFIEE_PREPARATION
    assert all(ligne.quantite_recue_chauffeur is None for ligne in lignes(chargement))


def test_reception_refusee_avant_la_verification(tournee, personnes):
    chargement = affecter(tournee, personnes)
    with activer_utilisateur(tournee.chauffeur), pytest.raises(ActionImpossible):
        services.confirmer_reception(chargement, recues(chargement, [60, 20]))


def test_contestation_du_chauffeur_acceptee(tournee, personnes):
    """§10 : écart consigné avant le départ, validé par un responsable."""
    premier = verifier(affecter(tournee, personnes), personnes)
    with activer_utilisateur(tournee.chauffeur):
        services.confirmer_reception(premier, recues(premier, [60, 20]))
    assert statut(tournee) == StatutTournee.DEPART_AUTORISE
    # Seconde commande ajoutée puis contestée : la tournée revient en chargement.
    second = verifier(affecter(tournee, personnes), personnes)
    with activer_utilisateur(tournee.chauffeur):
        with pytest.raises(ActionImpossible, match="motif"):
            services.contester_reception(second, recues(second, [50, 20]), "")
        services.contester_reception(second, recues(second, [50, 20]), "10 manquants")
    assert statut(second) == StatutChargement.ECART_CONSTATE
    assert statut(tournee) == StatutTournee.EN_CHARGEMENT
    demande = exceptions(second, "ecart", EN_ATTENTE).get()
    assert demande.contenu["origine"] == "livreur" and demande.demandee_par == tournee.chauffeur
    with activer_utilisateur(personnes.gestionnaire):
        services.decider_exception_chargement(demande, accepter=True)
    with activer_utilisateur(tournee.chauffeur):
        services.confirmer_reception(second, recues(second, [50, 20]))
    assert statut(second) == StatutChargement.RECUE_CHAUFFEUR
    assert statut(tournee) == StatutTournee.DEPART_AUTORISE


def test_contestation_refusee_au_preparateur(tournee, personnes):
    chargement = verifier(affecter(tournee, personnes), personnes)
    with activer_utilisateur(personnes.preparateur), pytest.raises(PermissionDenied):
        services.contester_reception(chargement, recues(chargement, [50, 20]), "manque")


def test_prestataire_avec_compte_confirme_lui_meme(entreprise, personnes):
    compte = UtilisateurFactory(entreprise=entreprise, roles=[R.PRESTATAIRE])
    prestataire = PrestataireFactory(entreprise=entreprise, compte=compte)
    tournee = TourneeFactory(
        entreprise=entreprise, date=DEMAIN, chauffeur=None, prestataire=prestataire
    )
    chargement = verifier(affecter(tournee, personnes), personnes)
    with activer_utilisateur(compte):
        services.confirmer_reception(chargement, recues(chargement, [60, 20]))
    assert statut(chargement) == StatutChargement.RECUE_CHAUFFEUR


def test_prestataire_sans_compte_reception_validee_par_un_autre(entreprise, personnes):
    """Décision 8.3 : le gestionnaire saisit, un autre responsable valide."""
    prestataire = PrestataireFactory(entreprise=entreprise)
    tournee = TourneeFactory(
        entreprise=entreprise, date=DEMAIN, chauffeur=None, prestataire=prestataire
    )
    chargement = verifier(affecter(tournee, personnes), personnes)
    with activer_utilisateur(personnes.gestionnaire):
        services.confirmer_reception(chargement, recues(chargement, [60, 20]))
    assert statut(chargement) == StatutChargement.VERIFIEE_PREPARATION
    demande = exceptions(chargement, "reception", EN_ATTENTE).get()
    with activer_utilisateur(personnes.gestionnaire), pytest.raises(ValidationPropreDemande):
        services.decider_exception_chargement(demande, accepter=True)
    with activer_utilisateur(personnes.direction):
        services.decider_exception_chargement(demande, accepter=True)
    chargement.refresh_from_db()
    assert chargement.statut == StatutChargement.RECUE_CHAUFFEUR
    assert chargement.recu_par == personnes.gestionnaire
    assert statut(tournee) == StatutTournee.DEPART_AUTORISE


# --- Exception de départ ----------------------------------------------------------------


def test_exception_de_depart(tournee, personnes):
    recu = verifier(affecter(tournee, personnes), personnes)
    with activer_utilisateur(tournee.chauffeur):
        services.confirmer_reception(recu, recues(recu, [60, 20]))
    bloque = affecter(tournee, personnes)
    assert statut(tournee) == StatutTournee.EN_CHARGEMENT
    with activer_utilisateur(personnes.gestionnaire):
        with pytest.raises(ActionImpossible, match="motif"):
            services.demander_exception_depart(bloque, "")
        demande = services.demander_exception_depart(bloque, "Client prévenu, livré demain")
    with activer_utilisateur(personnes.direction):
        services.decider_exception_chargement(demande, accepter=True)
    assert statut(tournee) == StatutTournee.DEPART_AUTORISE


@pytest.mark.parametrize("personne", ["direction", "preparateur", "autre_chauffeur"])
def test_exception_de_depart_demandee_par_le_gestionnaire_seul(tournee, personnes, personne):
    chargement = affecter(tournee, personnes)
    with activer_utilisateur(getattr(personnes, personne)), pytest.raises(PermissionDenied):
        services.demander_exception_depart(chargement, "pressé")


def test_exception_de_depart_inutile_si_recu(tournee, personnes):
    chargement = verifier(affecter(tournee, personnes), personnes)
    with activer_utilisateur(tournee.chauffeur):
        services.confirmer_reception(chargement, recues(chargement, [60, 20]))
    with activer_utilisateur(personnes.gestionnaire), pytest.raises(ActionImpossible):
        services.demander_exception_depart(chargement, "motif")


# --- Recette §22-17 et §22-18 -----------------------------------------------------------


def test_recette_22_17_preparateur_puis_chauffeur(tournee, personnes):
    chargement = affecter(tournee, personnes)
    verifier(chargement, personnes)
    chargement.refresh_from_db()
    assert chargement.statut == StatutChargement.VERIFIEE_PREPARATION  # pas encore reçue
    with activer_utilisateur(tournee.chauffeur):
        services.confirmer_reception(chargement, recues(chargement, [60, 20]))
    chargement.refresh_from_db()
    assert chargement.statut == StatutChargement.RECUE_CHAUFFEUR
    assert chargement.verifie_par == personnes.preparateur
    assert chargement.recu_par == tournee.chauffeur


def test_recette_22_18_heure_identites_ecarts_et_depart_bloque(tournee, personnes):
    chargement = affecter(tournee, personnes)
    compter(chargement, [30, 20], personnes.preparateur, motif=MotifEcart.PRODUCTION_INSUFFISANTE)
    with activer_utilisateur(personnes.preparateur):
        services.signaler_ecart(chargement)
    # Pas de départ sans confirmation ni exception approuvée.
    tournee.refresh_from_db()
    assert not can_proceed(tournee.autoriser_depart)
    # Marchandise complétée : recompte conforme, puis réception du chauffeur.
    with activer_utilisateur(personnes.preparateur):
        services.completer_chargement(chargement)
    verifier(chargement, personnes)
    tournee.refresh_from_db()
    assert not can_proceed(tournee.autoriser_depart)
    with activer_utilisateur(tournee.chauffeur):
        services.confirmer_reception(chargement, recues(chargement, [60, 20]))
    chargement = Chargement.objects.get(pk=chargement.pk)
    assert chargement.verifie_le and chargement.recu_le
    assert (chargement.verifie_par, chargement.recu_par) == (
        personnes.preparateur,
        tournee.chauffeur,
    )
    # L'écart reste dans l'historique : 30 préparés, puis 60.
    premiere = lignes(chargement)[0]
    historique = list(
        HistoriqueLigneChargement.objects.filter(pgh_obj=premiere)
        .order_by("pgh_id")
        .values_list("quantite_preparee", "motif_ecart")
    )
    assert (Decimal(30), MotifEcart.PRODUCTION_INSUFFISANTE) in historique
    assert historique[-1][0] == Decimal(60)
    assert statut(tournee) == StatutTournee.DEPART_AUTORISE


# --- Isolation ------------------------------------------------------------------------


def test_isolation_entre_entreprises(tournee, personnes):
    chargement, demande = _ecart_signale(tournee, personnes)
    with activer_entreprise(None):
        autre = EntrepriseFactory()
        etranger = UtilisateurFactory(entreprise=autre, roles=[R.GESTIONNAIRE])
    with activer_entreprise(autre), activer_utilisateur(etranger):
        with pytest.raises(ObjectDoesNotExist):
            services.decider_exception_chargement(demande, accepter=True)
        assert not Validation.objects.exists()
