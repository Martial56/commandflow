"""Services de la tournée : création, livreur, affectation, retrait, ordre de passage."""

import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied

from clients.tests.factories import AdresseFactory
from commandes.models import Commande, Statut
from commandes.tests.factories import CommandeFactory, LigneCommandeFactory
from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from logistique import services
from logistique.models import (
    Chargement,
    LigneChargement,
    StatutChargement,
    StatutTournee,
    Tournee,
)
from logistique.services import ActionImpossible
from logistique.tests.factories import PrestataireFactory, TourneeFactory, VehiculeFactory
from noyau.contexte import AutreEntreprise, activer_entreprise, activer_utilisateur

pytestmark = pytest.mark.django_db
R = Role
DEMAIN = datetime.date.today() + datetime.timedelta(days=1)


def forcer(objet, statut):
    type(objet).toutes_entreprises.filter(pk=objet.pk).update(statut=statut)
    objet.refresh_from_db()
    return objet


@pytest.fixture
def entreprise():
    return EntrepriseFactory()


@pytest.fixture
def gestionnaire(entreprise):
    utilisateur = UtilisateurFactory(entreprise=entreprise, roles=[R.GESTIONNAIRE])
    with activer_entreprise(entreprise), activer_utilisateur(utilisateur):
        yield utilisateur


@pytest.fixture
def tournee(entreprise, gestionnaire):
    return TourneeFactory(entreprise=entreprise, date=DEMAIN)


def commande_prete(entreprise, *, creneau="06h–08h", commune="Cocody", date=DEMAIN):
    adresse = AdresseFactory(client__entreprise=entreprise, commune=commune)
    commande = CommandeFactory(
        adresse=adresse, date_livraison=date, creneau=creneau, avec_ligne=False
    )
    LigneCommandeFactory(commande=commande, quantite=Decimal(60))
    LigneCommandeFactory(commande=commande, quantite=Decimal(20))
    return forcer(commande, Statut.PRETE_CHARGEMENT)


def chauffeur(entreprise):
    return UtilisateurFactory(entreprise=entreprise, roles=[R.CHAUFFEUR])


# --- creer_tournee ------------------------------------------------------------


@pytest.mark.parametrize("role", [R.GESTIONNAIRE, R.DIRECTION])
def test_creer_tournee_numerotee(entreprise, role):
    responsable = UtilisateurFactory(entreprise=entreprise, roles=[role])
    with activer_entreprise(entreprise), activer_utilisateur(responsable):
        tournee = services.creer_tournee(date=DEMAIN, chauffeur=chauffeur(entreprise))
        autre = services.creer_tournee(
            date=DEMAIN, prestataire=PrestataireFactory(entreprise=entreprise)
        )
    assert tournee.numero == f"TRN-{DEMAIN.year}-000001"
    assert autre.numero == f"TRN-{DEMAIN.year}-000002"
    assert tournee.statut == StatutTournee.PLANIFIEE and tournee.cree_par == responsable


@pytest.mark.parametrize("role", [R.PREPARATION, R.CHAUFFEUR, R.COMMERCIAL, R.PRODUCTION])
def test_creer_tournee_refuse_au_mauvais_role(entreprise, role):
    utilisateur = UtilisateurFactory(entreprise=entreprise, roles=[role])
    with activer_entreprise(entreprise), activer_utilisateur(utilisateur):
        with pytest.raises(PermissionDenied):
            services.creer_tournee(date=DEMAIN, chauffeur=chauffeur(entreprise))


def test_creer_tournee_exige_un_seul_livreur(entreprise, gestionnaire):
    with pytest.raises(ActionImpossible):
        services.creer_tournee(date=DEMAIN)
    with pytest.raises(ActionImpossible):
        services.creer_tournee(
            date=DEMAIN,
            chauffeur=chauffeur(entreprise),
            prestataire=PrestataireFactory(entreprise=entreprise),
        )


def test_creer_tournee_refuse_un_livreur_invalide(entreprise, gestionnaire):
    preparateur = UtilisateurFactory(entreprise=entreprise, roles=[R.PREPARATION])
    with pytest.raises(ActionImpossible, match="chauffeur actif"):
        services.creer_tournee(date=DEMAIN, chauffeur=preparateur)
    with pytest.raises(ActionImpossible, match="désactivé"):
        services.creer_tournee(
            date=DEMAIN, prestataire=PrestataireFactory(entreprise=entreprise, actif=False)
        )
    with pytest.raises(ActionImpossible, match="désactivé"):
        services.creer_tournee(
            date=DEMAIN,
            chauffeur=chauffeur(entreprise),
            vehicule=VehiculeFactory(entreprise=entreprise, actif=False),
        )
    assert not Tournee.objects.exists()


def test_creer_tournee_refuse_un_chauffeur_d_une_autre_entreprise(gestionnaire):
    with activer_entreprise(None):
        etranger = chauffeur(EntrepriseFactory())
    with pytest.raises(AutreEntreprise):
        services.creer_tournee(date=DEMAIN, chauffeur=etranger)


# --- changer_livreur ----------------------------------------------------------


def test_changer_livreur_avant_le_depart(entreprise, tournee):
    prestataire = PrestataireFactory(entreprise=entreprise)
    vehicule = VehiculeFactory(entreprise=entreprise)
    services.changer_livreur(tournee, prestataire=prestataire, vehicule=vehicule)
    tournee.refresh_from_db()
    assert tournee.chauffeur is None and tournee.prestataire == prestataire
    assert tournee.vehicule == vehicule


def test_changer_livreur_refuse_si_une_commande_est_recue(entreprise, tournee):
    chargement = services.affecter_commande(tournee, commande_prete(entreprise))
    forcer(chargement, StatutChargement.RECUE_CHAUFFEUR)
    with pytest.raises(ActionImpossible, match="déjà reçues"):
        services.changer_livreur(tournee, chauffeur=chauffeur(entreprise))


def test_changer_livreur_refuse_apres_le_depart(entreprise, tournee):
    forcer(tournee, StatutTournee.EN_COURS)
    with pytest.raises(ActionImpossible, match="partie"):
        services.changer_livreur(tournee, chauffeur=chauffeur(entreprise))


def test_changer_livreur_refuse_au_preparateur(entreprise, tournee):
    with activer_utilisateur(UtilisateurFactory(entreprise=entreprise, roles=[R.PREPARATION])):
        with pytest.raises(PermissionDenied):
            services.changer_livreur(tournee, chauffeur=chauffeur(entreprise))


# --- affecter_commande --------------------------------------------------------


def test_affecter_cree_le_chargement_et_ses_lignes(entreprise, tournee):
    commande = commande_prete(entreprise)
    assert list(services.commandes_affectables(DEMAIN)) == [commande]
    chargement = services.affecter_commande(tournee, commande)
    assert chargement.statut == StatutChargement.A_PREPARER and chargement.ordre_passage == 1
    assert sorted(chargement.lignes.values_list("quantite_attendue", flat=True)) == [20, 60]
    assert list(services.commandes_affectables(DEMAIN)) == []
    commande.refresh_from_db()
    assert commande.statut == Statut.PRETE_CHARGEMENT  # inchangée jusqu'au départ


def test_affecter_ignore_les_lignes_retirees(entreprise, tournee):
    commande = commande_prete(entreprise)
    ligne = commande.lignes.first()
    ligne.retiree = True
    ligne.save()
    chargement = services.affecter_commande(tournee, commande)
    assert chargement.lignes.count() == 1


def test_affecter_numerote_l_ordre_de_passage(entreprise, tournee):
    premier = services.affecter_commande(tournee, commande_prete(entreprise))
    second = services.affecter_commande(tournee, commande_prete(entreprise))
    assert (premier.ordre_passage, second.ordre_passage) == (1, 2)


@pytest.mark.parametrize("statut", [Statut.EN_PREPARATION, Statut.EN_LIVRAISON, Statut.ANNULEE])
def test_affecter_refuse_une_commande_pas_prete(entreprise, tournee, statut):
    commande = forcer(commande_prete(entreprise), statut)
    with pytest.raises(ActionImpossible, match="pas prête"):
        services.affecter_commande(tournee, commande)


def test_affecter_refuse_une_autre_date(entreprise, tournee):
    commande = commande_prete(entreprise, date=DEMAIN + datetime.timedelta(days=1))
    with pytest.raises(ActionImpossible, match="jour de la tournée"):
        services.affecter_commande(tournee, commande)


def test_affecter_refuse_une_commande_deja_affectee(entreprise, tournee):
    commande = commande_prete(entreprise)
    services.affecter_commande(tournee, commande)
    autre = TourneeFactory(entreprise=entreprise, date=DEMAIN)
    with pytest.raises(ActionImpossible, match="déjà affectée"):
        services.affecter_commande(autre, commande)


def test_affecter_refuse_apres_le_depart(entreprise, tournee):
    forcer(tournee, StatutTournee.EN_COURS)
    with pytest.raises(ActionImpossible, match="partie"):
        services.affecter_commande(tournee, commande_prete(entreprise))


@pytest.mark.parametrize("role", [R.PREPARATION, R.CHAUFFEUR, R.COMMERCIAL])
def test_affecter_refuse_au_mauvais_role(entreprise, tournee, role):
    commande = commande_prete(entreprise)
    with activer_utilisateur(UtilisateurFactory(entreprise=entreprise, roles=[role])):
        with pytest.raises(PermissionDenied):
            services.affecter_commande(tournee, commande)


def test_affecter_rouvre_une_tournee_au_depart_autorise(entreprise, tournee):
    """Cascade : une commande ajoutée après l'autorisation de départ rouvre le chargement."""
    premier = services.affecter_commande(tournee, commande_prete(entreprise))
    forcer(premier, StatutChargement.RECUE_CHAUFFEUR)
    forcer(tournee, StatutTournee.DEPART_AUTORISE)
    services.affecter_commande(tournee, commande_prete(entreprise))
    tournee.refresh_from_db()
    assert tournee.statut == StatutTournee.EN_CHARGEMENT


def test_affecter_annule_tout_en_cas_d_erreur(entreprise, tournee, monkeypatch):
    commande = commande_prete(entreprise)

    def echec(*args, **kwargs):
        raise RuntimeError("panne")

    monkeypatch.setattr(LigneChargement.objects, "create", echec)
    with pytest.raises(RuntimeError):
        services.affecter_commande(tournee, commande)
    assert not Chargement.objects.exists()


# --- retirer_commande ---------------------------------------------------------


def test_retirer_rend_la_commande_affectable_et_renumerote(entreprise, tournee):
    premier = services.affecter_commande(tournee, commande_prete(entreprise))
    second = services.affecter_commande(tournee, commande_prete(entreprise))
    services.retirer_commande(premier)
    premier.refresh_from_db()
    second.refresh_from_db()
    assert premier.statut == StatutChargement.RETIRE
    assert second.ordre_passage == 1
    assert list(services.commandes_affectables(DEMAIN)) == [premier.commande]
    # Réaffectable, y compris dans la même tournée (historique conservé).
    nouveau = services.affecter_commande(tournee, premier.commande)
    assert nouveau.pk != premier.pk and nouveau.ordre_passage == 2


def test_retirer_autorise_le_depart_des_autres(entreprise, tournee):
    """Cascade : retirer la seule commande non reçue autorise le départ."""
    recu = services.affecter_commande(tournee, commande_prete(entreprise))
    bloque = services.affecter_commande(tournee, commande_prete(entreprise))
    forcer(recu, StatutChargement.RECUE_CHAUFFEUR)
    forcer(tournee, StatutTournee.EN_CHARGEMENT)
    services.retirer_commande(bloque)
    tournee.refresh_from_db()
    assert tournee.statut == StatutTournee.DEPART_AUTORISE


def test_retirer_refuse_apres_le_depart(entreprise, tournee):
    chargement = services.affecter_commande(tournee, commande_prete(entreprise))
    forcer(tournee, StatutTournee.EN_COURS)
    with pytest.raises(ActionImpossible, match="partie"):
        services.retirer_commande(chargement)


def test_retirer_refuse_au_preparateur(entreprise, tournee):
    chargement = services.affecter_commande(tournee, commande_prete(entreprise))
    with activer_utilisateur(UtilisateurFactory(entreprise=entreprise, roles=[R.PREPARATION])):
        with pytest.raises(PermissionDenied):
            services.retirer_commande(chargement)


def test_retirer_deux_fois_refuse(entreprise, tournee):
    chargement = services.affecter_commande(tournee, commande_prete(entreprise))
    services.retirer_commande(chargement)
    with pytest.raises(ActionImpossible, match="déjà retirée"):
        services.retirer_commande(chargement)


# --- Ordre de passage ------------------------------------------------------------


def test_proposer_ordre_par_creneau_puis_commune(entreprise, tournee):
    tard = services.affecter_commande(tournee, commande_prete(entreprise, creneau="10h–12h"))
    yopougon = services.affecter_commande(tournee, commande_prete(entreprise, commune="Yopougon"))
    cocody = services.affecter_commande(tournee, commande_prete(entreprise, commune="Cocody"))
    assert services.proposer_ordre(tournee) == [cocody, yopougon, tard]
    ordres = dict(Chargement.objects.values_list("pk", "ordre_passage"))
    assert [ordres[c.pk] for c in (cocody, yopougon, tard)] == [1, 2, 3]


def test_reordonner(entreprise, tournee):
    premier = services.affecter_commande(tournee, commande_prete(entreprise))
    second = services.affecter_commande(tournee, commande_prete(entreprise))
    services.reordonner(tournee, [second, premier])
    assert list(tournee.chargements_actifs()) == [second, premier]


def test_reordonner_exige_toutes_les_commandes(entreprise, tournee):
    premier = services.affecter_commande(tournee, commande_prete(entreprise))
    services.affecter_commande(tournee, commande_prete(entreprise))
    with pytest.raises(ActionImpossible, match="toutes les commandes"):
        services.reordonner(tournee, [premier])


def test_reordonner_refuse_au_chauffeur(entreprise, tournee):
    premier = services.affecter_commande(tournee, commande_prete(entreprise))
    with activer_utilisateur(tournee.chauffeur):
        with pytest.raises(PermissionDenied):
            services.reordonner(tournee, [premier])


# --- Isolation ------------------------------------------------------------------------


def test_isolation_entre_entreprises(entreprise, tournee):
    with activer_entreprise(None):
        autre = EntrepriseFactory()
        commande_autre = commande_prete(autre)
        tournee_autre = TourneeFactory(entreprise=autre, date=DEMAIN)
    assert list(Tournee.objects.all()) == [tournee]
    assert list(services.commandes_affectables(DEMAIN)) == []
    with pytest.raises(Tournee.DoesNotExist):
        services.affecter_commande(tournee_autre, commande_autre)
    with pytest.raises(Commande.DoesNotExist):
        services.affecter_commande(tournee, commande_autre)
