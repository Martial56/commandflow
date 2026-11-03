"""Services de livraison (§6, §7, §12, décisions 8.4) : cascades vers la commande et la
tournée, annulation complète en cas d'erreur, recette §22-19, §22-20 et §22-21."""

import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from commandes import services as commandes
from commandes.models import Statut
from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from logistique import services
from logistique.models import (
    AccordClient,
    DecisionRetour,
    EtatRetour,
    Incident,
    Livraison,
    MotifEchec,
    MoyenAccord,
    Preuve,
    Reliquat,
    StatutLivraison,
    StatutTournee,
    TypeIncident,
    TypePreuve,
)
from logistique.services import ActionImpossible, Resultat
from logistique.tests.factories import PrestataireFactory, TourneeFactory
from logistique.tests.test_depart import demarrer, recue
from logistique.tests.test_services_controle import statut
from logistique.tests.test_services_tournee import DEMAIN
from noyau.contexte import activer_entreprise, activer_utilisateur
from noyau.models import PieceJointe, Validation
from noyau.services import ValidationPropreDemande
from production.models import PlanProduction

pytestmark = pytest.mark.django_db
R = Role
APRES_DEMAIN = DEMAIN + datetime.timedelta(days=1)


@pytest.fixture
def entreprise():
    entreprise = EntrepriseFactory()
    with activer_entreprise(entreprise):
        yield entreprise


@pytest.fixture
def tournee(entreprise, personnes):
    return TourneeFactory(entreprise=entreprise, date=DEMAIN)


@pytest.fixture
def livraisons(tournee, personnes):
    """Deux commandes parties (lignes prévues 60 et 20 chacune)."""
    chargements = [recue(tournee, personnes), recue(tournee, personnes)]
    demarrer(tournee)
    return [Livraison.objects.get(chargement=chargement) for chargement in chargements]


@pytest.fixture
def livraison(livraisons):
    return livraisons[0]


def quantites(livraison, valeurs):
    return dict(zip(livraison.lignes.order_by("pk"), valeurs, strict=True))


def cloturer(livraison, resultat, par=None, **kwargs):
    with activer_utilisateur(par or livraison.tournee.chauffeur):
        return services.cloturer_livraison(livraison, resultat, **kwargs)


def livrer(livraison, par=None, **kwargs):
    kwargs.setdefault("receptionnaire_nom", "Mme Bamba")
    return cloturer(
        livraison, Resultat.LIVREE, par, quantites=quantites(livraison, [60, 20]), **kwargs
    )


def partielle(livraison, **kwargs):
    kwargs.setdefault("accord", {"nom_client": "M. Kouassi", "moyen": MoyenAccord.ORAL})
    return cloturer(
        livraison,
        Resultat.PARTIELLE,
        quantites=quantites(livraison, [40, 20]),
        motif=MotifEchec.MARCHANDISE_MANQUANTE,
        receptionnaire_nom="Mme Bamba",
        **kwargs,
    )


def photo():
    return SimpleUploadedFile("bordereau.jpg", b"\xff\xd8\xff photo", content_type="image/jpeg")


# --- Arrivée -------------------------------------------------------------------------


def test_arrivee_avec_position(livraison):
    with activer_utilisateur(livraison.tournee.chauffeur):
        services.arriver_chez_client(livraison, latitude="5.3364", longitude="-4.0267")
    livraison.refresh_from_db()
    assert livraison.statut == StatutLivraison.SUR_PLACE and livraison.arrivee_le
    assert livraison.latitude == Decimal("5.336400")


def test_arrivee_refusee_a_un_autre_chauffeur(livraison, personnes):
    with activer_utilisateur(personnes.autre_chauffeur):
        with pytest.raises(PermissionDenied):
            services.arriver_chez_client(livraison)
    assert statut(livraison) == StatutLivraison.EN_ATTENTE


def test_arrivee_exige_la_position_si_l_option_l_impose(entreprise, livraison):
    entreprise.options = {"preuves": {"restaurant": ["gps"]}}
    entreprise.save()
    with activer_utilisateur(livraison.tournee.chauffeur):
        with pytest.raises(ActionImpossible, match="Position GPS requise"):
            services.arriver_chez_client(livraison)
        services.arriver_chez_client(livraison, latitude=5.3, longitude=-4.0)
    assert statut(livraison) == StatutLivraison.SUR_PLACE


# --- Livrée (§22-19) -----------------------------------------------------------------


def test_livree_cascade_vers_la_commande(livraisons):
    premiere, seconde = livraisons
    livrer(premiere, numero_bordereau="BL-1284", latitude=5.3, longitude=-4.0)
    premiere.refresh_from_db()
    assert premiere.statut == StatutLivraison.LIVREE and premiere.traitee_le
    assert premiere.arrivee_le  # arrivée non signalée : datée de la validation
    assert [ligne.quantite_livree for ligne in premiere.lignes.order_by("pk")] == [60, 20]
    assert statut(premiere.commande) == Statut.LIVREE
    assert statut(seconde.commande) == Statut.EN_LIVRAISON
    assert statut(premiere.tournee) == StatutTournee.EN_COURS


def test_livree_avec_la_photo_du_bordereau(livraison):
    livrer(livraison, receptionnaire_nom="", photo=photo())
    preuve = Preuve.objects.get(livraison=livraison)
    assert preuve.type == TypePreuve.PHOTO_BORDEREAU
    assert preuve.piece.objet == livraison and preuve.piece.type == PieceJointe.Type.PHOTO
    assert statut(livraison) == StatutLivraison.LIVREE


def test_livree_avec_signature(entreprise, livraison):
    entreprise.options = {"preuves": {"restaurant": ["signature"]}}
    entreprise.save()
    signature = SimpleUploadedFile("signature.png", b"\x89PNG", content_type="image/png")
    livrer(livraison, receptionnaire_nom="", signature=signature)
    assert Preuve.objects.get(livraison=livraison).type == TypePreuve.SIGNATURE


def test_livree_refusee_sans_preuve(livraison):
    with pytest.raises(ActionImpossible) as erreur:
        livrer(livraison, receptionnaire_nom="")
    assert str(erreur.value) == ("Preuve manquante : photo du bordereau ou nom du réceptionnaire.")
    assert statut(livraison) == StatutLivraison.EN_ATTENTE
    assert all(ligne.quantite_livree is None for ligne in livraison.lignes.all())


def test_livree_refusee_si_une_quantite_manque(livraison):
    with pytest.raises(ActionImpossible, match="choisir « Partielle »"):
        cloturer(
            livraison,
            Resultat.LIVREE,
            quantites=quantites(livraison, [50, 20]),
            receptionnaire_nom="Mme Bamba",
        )
    assert statut(livraison) == StatutLivraison.EN_ATTENTE


def test_jamais_plus_que_le_prevu(livraison):
    with pytest.raises(ActionImpossible, match="jamais plus que le prévu"):
        cloturer(
            livraison,
            Resultat.LIVREE,
            quantites=quantites(livraison, [61, 20]),
            receptionnaire_nom="Mme Bamba",
        )


def test_quantite_obligatoire_sur_chaque_ligne(livraison):
    premiere = livraison.lignes.order_by("pk").first()
    with pytest.raises(ActionImpossible, match="chaque produit"):
        cloturer(livraison, Resultat.LIVREE, quantites={premiere: 60}, receptionnaire_nom="X")


def test_livraison_deja_traitee(livraison):
    livrer(livraison)
    with pytest.raises(ActionImpossible, match="déjà traitée"):
        livrer(livraison)


# --- Partielle (§22-20) --------------------------------------------------------------


def test_partielle_cree_le_reliquat(livraison):
    partielle(livraison)
    assert statut(livraison) == StatutLivraison.PARTIELLEMENT_LIVREE
    accord = AccordClient.objects.get(livraison=livraison)
    assert (accord.nom_client, accord.moyen) == ("M. Kouassi", MoyenAccord.ORAL)
    reliquat = Reliquat.objects.get()
    assert reliquat.commande_origine == livraison.commande and reliquat.quantite == 20
    assert reliquat.ligne_commande == livraison.lignes.order_by("pk").first().ligne_commande
    assert not reliquat.traite and reliquat.nouvelle_date is None
    assert statut(livraison.commande) == Statut.PARTIELLEMENT_LIVREE


def test_partielle_exige_l_accord_du_client(livraison):
    with pytest.raises(ActionImpossible, match="accord du client"):
        partielle(livraison, accord=None)
    assert not Reliquat.objects.exists()
    assert statut(livraison.commande) == Statut.EN_LIVRAISON


def test_partielle_exige_un_motif(livraison):
    with pytest.raises(ActionImpossible, match="motif"):
        cloturer(
            livraison,
            Resultat.PARTIELLE,
            quantites=quantites(livraison, [40, 20]),
            receptionnaire_nom="Mme Bamba",
            accord={"nom_client": "M. Kouassi", "moyen": MoyenAccord.ORAL},
        )


def test_partielle_annulee_en_entier_en_cas_d_erreur(livraison, monkeypatch):
    def panne(**kwargs):
        raise RuntimeError("panne")

    monkeypatch.setattr(Reliquat.objects, "create", panne)
    with pytest.raises(RuntimeError):
        partielle(livraison)
    assert statut(livraison) == StatutLivraison.EN_ATTENTE
    assert statut(livraison.commande) == Statut.EN_LIVRAISON
    assert not AccordClient.objects.exists()
    assert all(ligne.quantite_livree is None for ligne in livraison.lignes.all())


# --- Échec (§22-21) ------------------------------------------------------------------


def test_echec_passe_la_commande_en_anomalie(livraison):
    cloturer(livraison, Resultat.ECHEC, motif=MotifEchec.CLIENT_ABSENT, commentaire="Fermé")
    livraison.refresh_from_db()
    assert livraison.statut == StatutLivraison.ECHOUEE
    assert (livraison.motif_echec, livraison.commentaire) == (MotifEchec.CLIENT_ABSENT, "Fermé")
    assert statut(livraison.commande) == Statut.EN_ANOMALIE


def test_echec_exige_un_motif(livraison):
    with pytest.raises(ActionImpossible, match="motif"):
        cloturer(livraison, Resultat.ECHEC)
    assert statut(livraison) == StatutLivraison.EN_ATTENTE


def test_tournee_terminee_au_dernier_arret(livraisons):
    premiere, seconde = livraisons
    cloturer(premiere, Resultat.ECHEC, motif=MotifEchec.REFUS)
    livrer(seconde)
    tournee = premiere.tournee
    assert statut(tournee) == StatutTournee.TERMINEE
    tournee.refresh_from_db()
    assert tournee.fin_le


def test_livrer_refuse_a_un_autre_chauffeur(livraison, personnes):
    with pytest.raises(PermissionDenied):
        livrer(livraison, par=personnes.autre_chauffeur)


# --- Report et abandon ---------------------------------------------------------------


@pytest.fixture
def echouee(livraison):
    cloturer(livraison, Resultat.ECHEC, motif=MotifEchec.CLIENT_ABSENT)
    livraison.refresh_from_db()
    return livraison


def test_report_relance_la_commande_a_la_nouvelle_date(echouee, personnes):
    with activer_utilisateur(personnes.gestionnaire):
        services.reporter_livraison(echouee, APRES_DEMAIN, consigne="Livrer avant 9 h")
    echouee.refresh_from_db()
    assert echouee.statut == StatutLivraison.REPORTEE and echouee.reportee_au == APRES_DEMAIN
    commande = echouee.commande
    commande.refresh_from_db()
    assert commande.statut == Statut.A_PLANIFIER
    assert (commande.date_livraison, commande.instructions) == (APRES_DEMAIN, "Livrer avant 9 h")
    # Décision 8.4 : la commande est reproduite dans le plan de la nouvelle date.
    plan = PlanProduction.objects.get(date_livraison=APRES_DEMAIN)
    assert sum(besoin.quantite_commandee for besoin in plan.besoins.all()) == 80


@pytest.mark.parametrize("personne", ["direction", "autre_chauffeur"])
def test_report_reserve_au_gestionnaire(echouee, personnes, personne):
    with activer_utilisateur(getattr(personnes, personne)):
        with pytest.raises(PermissionDenied):
            services.reporter_livraison(echouee, APRES_DEMAIN)
    with activer_utilisateur(echouee.tournee.chauffeur):
        with pytest.raises(PermissionDenied):
            services.reporter_livraison(echouee, APRES_DEMAIN)


def test_report_refuse_une_date_passee(echouee, personnes):
    hier = datetime.date.today() - datetime.timedelta(days=1)
    with activer_utilisateur(personnes.gestionnaire):
        with pytest.raises(ActionImpossible, match="nouvelle date"):
            services.reporter_livraison(echouee, hier)
    assert statut(echouee) == StatutLivraison.ECHOUEE
    assert statut(echouee.commande) == Statut.EN_ANOMALIE


def test_abandon_valide_par_un_autre_responsable(echouee, personnes):
    with activer_utilisateur(personnes.gestionnaire):
        demande = commandes.demander_abandon(echouee.commande, "Client fermé définitivement")
        with pytest.raises(ValidationPropreDemande):
            commandes.decider_validation_commande(demande, accepter=True)
    assert statut(echouee.commande) == Statut.EN_ANOMALIE
    with activer_utilisateur(personnes.direction):
        commandes.decider_validation_commande(demande, accepter=True)
    commande = echouee.commande
    commande.refresh_from_db()
    assert commande.statut == Statut.ANNULEE
    assert commande.motif_annulation == "Client fermé définitivement"


def test_abandon_refuse_hors_anomalie(livraison, personnes):
    with activer_utilisateur(personnes.gestionnaire):
        with pytest.raises(commandes.ActionImpossible, match="en anomalie"):
            commandes.demander_abandon(livraison.commande, "x")


def test_abandon_exige_un_motif(echouee, personnes):
    with activer_utilisateur(personnes.gestionnaire):
        with pytest.raises(ValidationError):
            commandes.demander_abandon(echouee.commande, " ")
    assert not Validation.objects.filter(action=Validation.Action.ANNULATION).exists()


def test_abandon_refuse_au_chauffeur(echouee):
    with activer_utilisateur(echouee.tournee.chauffeur):
        with pytest.raises(PermissionDenied):
            commandes.demander_abandon(echouee.commande, "x")


# --- Prestataire sans compte (décision 8.4) ------------------------------------------


@pytest.fixture
def pour_prestataire(entreprise, livraison):
    tournee = livraison.tournee
    tournee.chauffeur, tournee.prestataire = None, PrestataireFactory(entreprise=entreprise)
    tournee.save()
    livraison.refresh_from_db()
    return livraison


def test_prestataire_sans_compte_saisie_validee(pour_prestataire, personnes):
    livraison = pour_prestataire
    livrer(livraison, par=personnes.gestionnaire, photo=photo())
    assert statut(livraison) == StatutLivraison.EN_ATTENTE  # en attente de décision
    assert Preuve.objects.filter(livraison=livraison).exists()  # preuve transmise jointe
    demande = Validation.objects.get(decision=Validation.Decision.EN_ATTENTE)
    assert demande.contenu["objet"] == "livraison"
    with pytest.raises(ActionImpossible, match="attend déjà"):
        livrer(livraison, par=personnes.gestionnaire)
    with activer_utilisateur(personnes.direction):
        services.decider_livraison_prestataire(demande, accepter=True)
    assert statut(livraison) == StatutLivraison.LIVREE
    assert statut(livraison.commande) == Statut.LIVREE


def test_prestataire_sans_compte_saisie_refusee(pour_prestataire, personnes):
    livraison = pour_prestataire
    cloturer(livraison, Resultat.ECHEC, par=personnes.gestionnaire, motif=MotifEchec.REFUS)
    demande = Validation.objects.get(decision=Validation.Decision.EN_ATTENTE)
    with activer_utilisateur(personnes.gestionnaire):
        with pytest.raises(ValidationPropreDemande):
            services.decider_livraison_prestataire(demande, accepter=True)
    with activer_utilisateur(personnes.direction):
        services.decider_livraison_prestataire(demande, accepter=False)
    assert statut(livraison) == StatutLivraison.EN_ATTENTE
    assert statut(livraison.commande) == Statut.EN_LIVRAISON


def test_prestataire_sans_compte_direction_ne_saisit_pas(pour_prestataire, personnes):
    with pytest.raises(PermissionDenied):
        livrer(pour_prestataire, par=personnes.direction)


# --- Retours (§22-21) ----------------------------------------------------------------


def produit(livraison, rang=0):
    return livraison.lignes.order_by("pk")[rang].ligne_commande.produit


def declarer(livraison, par=None, **kwargs):
    kwargs.setdefault("produit", produit(livraison))
    kwargs.setdefault("quantite", 5)
    kwargs.setdefault("motif", "Baguettes écrasées")
    kwargs.setdefault("etat", EtatRetour.ABIME)
    with activer_utilisateur(par or livraison.tournee.chauffeur):
        return services.declarer_retour(livraison, **kwargs)


def test_retour_declare_par_le_livreur_et_decide(livraison, personnes):
    livrer(livraison)
    retour = declarer(livraison)
    assert (retour.quantite, retour.etat, retour.decision) == (5, EtatRetour.ABIME, "")
    with activer_utilisateur(personnes.direction):
        services.decider_retour(retour, DecisionRetour.DESTRUCTION)
    retour.refresh_from_db()
    assert retour.decision == DecisionRetour.DESTRUCTION
    assert retour.decide_par == personnes.direction and retour.decide_le
    with activer_utilisateur(personnes.gestionnaire):
        with pytest.raises(ActionImpossible, match="déjà prise"):
            services.decider_retour(retour, DecisionRetour.REMISE_EN_VENTE)


def test_retour_declare_par_le_gestionnaire_au_depot(livraison, personnes):
    cloturer(livraison, Resultat.ECHEC, motif=MotifEchec.REFUS)
    retour = declarer(livraison, par=personnes.gestionnaire, quantite=60)
    assert retour.cree_par == personnes.gestionnaire


def test_retour_refuse_avant_la_livraison(livraison):
    with pytest.raises(ActionImpossible, match="traitée"):
        declarer(livraison)


@pytest.mark.parametrize("quantite", [0, 61])
def test_retour_quantite_invalide(livraison, quantite):
    livrer(livraison)
    with pytest.raises(ActionImpossible, match="Quantité"):
        declarer(livraison, quantite=quantite)


def test_retour_d_un_produit_hors_livraison(entreprise, livraison):
    from catalogue.tests.factories import ProduitFactory

    livrer(livraison)
    with pytest.raises(ActionImpossible, match="ne fait pas partie"):
        declarer(livraison, produit=ProduitFactory(entreprise=entreprise))


def test_retour_refuse_aux_autres(livraison, personnes):
    livrer(livraison)
    with pytest.raises(PermissionDenied):
        declarer(livraison, par=personnes.autre_chauffeur)
    retour = declarer(livraison)
    with activer_utilisateur(livraison.tournee.chauffeur):
        with pytest.raises(PermissionDenied):
            services.decider_retour(retour, DecisionRetour.DESTRUCTION)


# --- Incidents -----------------------------------------------------------------------


def test_incident_signale_pendant_la_tournee(livraison, personnes):
    tournee = livraison.tournee
    with activer_utilisateur(tournee.chauffeur):
        incident = services.signaler_incident(tournee, TypeIncident.PANNE, "Pneu crevé")
    assert (incident.tournee, incident.cree_par) == (tournee, tournee.chauffeur)
    assert statut(tournee) == StatutTournee.EN_COURS
    with activer_utilisateur(personnes.autre_chauffeur):
        with pytest.raises(PermissionDenied):
            services.signaler_incident(tournee, TypeIncident.PANNE)


def test_incident_refuse_hors_tournee_en_cours(tournee, personnes):
    recue(tournee, personnes)
    with activer_utilisateur(tournee.chauffeur):
        with pytest.raises(ActionImpossible, match="pendant la tournée"):
            services.signaler_incident(tournee, TypeIncident.ACCIDENT)
    assert not Incident.objects.exists()


# --- Isolation -----------------------------------------------------------------------


def test_isolation_entre_entreprises(livraison):
    with activer_entreprise(None):
        autre = EntrepriseFactory()
        etranger = UtilisateurFactory(entreprise=autre, roles=[R.CHAUFFEUR])
    with activer_entreprise(autre), activer_utilisateur(etranger):
        with pytest.raises(ObjectDoesNotExist):
            services.cloturer_livraison(livraison, Resultat.ECHEC, motif=MotifEchec.REFUS)
        assert not Livraison.objects.exists()
    assert statut(livraison) == StatutLivraison.EN_ATTENTE
