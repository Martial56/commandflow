"""Suivi d'une tournée partie sur l'écran Tournées (§6, §11, décision 8.4) : résultat des
arrêts, incidents, report et abandon, saisie d'un prestataire sans compte, ordre des
arrêts restants, pastille du menu."""

import datetime
import html
import re

import pytest
from django.urls import reverse

from commandes import services as commandes
from commandes.models import Statut
from comptes.models import Role
from entreprises.tests.factories import EntrepriseFactory
from logistique import alertes, services
from logistique.models import Livraison, MotifEchec, StatutLivraison, TypeIncident
from logistique.services import ActionImpossible, Resultat
from logistique.tests.factories import PrestataireFactory, TourneeFactory
from logistique.tests.test_depart import demarrer, recue
from logistique.tests.test_services_controle import statut
from logistique.tests.test_services_tournee import DEMAIN
from logistique.tests.test_transitions import utilisateur
from noyau.contexte import activer_entreprise, activer_utilisateur
from noyau.models import Validation

pytestmark = pytest.mark.django_db
APRES_DEMAIN = DEMAIN + datetime.timedelta(days=1)


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
    tournee = TourneeFactory(entreprise=entreprise, date=DEMAIN)
    for _ in range(3):
        recue(tournee, personnes)
    demarrer(tournee)
    return tournee


def livraisons(tournee):
    return list(
        Livraison.objects.filter(chargement__tournee=tournee).order_by(
            "chargement__ordre_passage", "pk"
        )
    )


def traiter(livraison, resultat=Resultat.LIVREE, par=None):
    with activer_utilisateur(par or livraison.tournee.chauffeur):
        if resultat == Resultat.ECHEC:
            return services.cloturer_livraison(
                livraison, resultat, motif=MotifEchec.CLIENT_ABSENT, commentaire="Fermé"
            )
        quantites = dict(zip(livraison.lignes.order_by("pk"), [60, 20], strict=True))
        return services.cloturer_livraison(
            livraison, resultat, quantites=quantites, receptionnaire_nom="Mme Bamba"
        )


def texte(reponse):
    contenu = html.unescape(reponse.content.decode())
    return " ".join(re.sub(r"<[^>]+>", " ", contenu).split())


def tableau(client):
    return client.get(reverse("ecran_tournees"), {"date": DEMAIN.isoformat()})


@pytest.fixture
def gestionnaire(client, personnes):
    client.force_login(personnes.gestionnaire)
    return client


# --- Progression et incidents --------------------------------------------------------


def test_progression_et_incident(gestionnaire, tournee):
    premiere, *_ = livraisons(tournee)
    traiter(premiere)
    with activer_utilisateur(tournee.chauffeur):
        services.signaler_incident(tournee, TypeIncident.PANNE, "Pneu crevé")
    page = texte(tableau(gestionnaire))
    assert "1/3 arrêts traité" in page
    assert f"{premiere.commande.client.nom} Livrée" in page
    assert "reçue par le chauffeur" not in page  # tournée partie : le résultat suffit
    assert "En attente" in page
    assert "Incident · panne à" in page and "Pneu crevé" in page


# --- Échec : report ou abandon -------------------------------------------------------


@pytest.fixture
def echouee(tournee):
    livraison = livraisons(tournee)[0]
    traiter(livraison, Resultat.ECHEC)
    livraison.refresh_from_db()
    return livraison


def test_echec_visible_avec_report_et_abandon(gestionnaire, echouee):
    page = texte(tableau(gestionnaire))
    assert "Échec : client absent · Fermé" in page
    assert "Reporter la livraison…" in page
    assert "Demander l'abandon de la commande…" in page


def test_reporter_depuis_l_ecran(gestionnaire, echouee):
    reponse = gestionnaire.post(
        reverse("livraison_reporter", args=[echouee.pk]),
        {"date": APRES_DEMAIN.isoformat(), "consigne": "Avant 9 h"},
    )
    assert reponse.url == f"{reverse('ecran_tournees')}?date={DEMAIN.isoformat()}"
    assert statut(echouee) == StatutLivraison.REPORTEE
    commande = echouee.commande
    commande.refresh_from_db()
    assert (commande.statut, commande.date_livraison) == (Statut.A_PLANIFIER, APRES_DEMAIN)
    page = texte(tableau(gestionnaire))
    assert "« À planifier »" in page and "Reportée au" in page
    assert "Reporter la livraison…" not in page


def test_reporter_sans_date(gestionnaire, echouee):
    gestionnaire.post(reverse("livraison_reporter", args=[echouee.pk]), {"date": ""})
    assert statut(echouee) == StatutLivraison.ECHOUEE
    assert "Choisir une nouvelle date" in texte(tableau(gestionnaire))


def test_la_direction_ne_reporte_pas(client, personnes, echouee):
    """Décision 8.4 : report réservé au Gestionnaire."""
    client.force_login(personnes.direction)
    assert "Reporter la livraison…" not in texte(tableau(client))
    reponse = client.post(
        reverse("livraison_reporter", args=[echouee.pk]), {"date": APRES_DEMAIN.isoformat()}
    )
    assert reponse.status_code == 403
    assert statut(echouee) == StatutLivraison.ECHOUEE


def test_abandon_demande_puis_decide(gestionnaire, client, personnes, echouee):
    gestionnaire.post(reverse("livraison_abandon", args=[echouee.pk]), {"motif": "Client parti"})
    demande = Validation.objects.get(action=Validation.Action.ANNULATION)
    page = texte(tableau(gestionnaire))
    assert f"Abandon demandé par {personnes.gestionnaire} : Client parti" in page
    assert "décider sur la fiche commande" in page
    assert "Demander l'abandon de la commande…" not in page
    with activer_utilisateur(personnes.direction):
        commandes.decider_validation_commande(demande, accepter=True)
    assert statut(echouee.commande) == Statut.ANNULEE


def test_abandon_sans_motif(gestionnaire, echouee):
    gestionnaire.post(reverse("livraison_abandon", args=[echouee.pk]), {"motif": " "})
    assert not Validation.objects.filter(action=Validation.Action.ANNULATION).exists()
    assert "Indiquer le motif de l'abandon." in texte(tableau(gestionnaire))


# --- Prestataire sans compte ----------------------------------------------------------


def test_saisie_du_prestataire_validee_sur_l_ecran(gestionnaire, client, entreprise, personnes):
    prestataire = PrestataireFactory(entreprise=entreprise)
    tournee = TourneeFactory(entreprise=entreprise, date=DEMAIN)
    recue(tournee, personnes)
    demarrer(tournee)
    tournee.refresh_from_db()  # partie : relue avant d'être enregistrée
    tournee.chauffeur, tournee.prestataire = None, prestataire
    tournee.save()
    assert "Saisir les livraisons du prestataire" in texte(tableau(gestionnaire))
    livraison = livraisons(tournee)[0]
    traiter(livraison, par=personnes.gestionnaire)
    page = texte(tableau(gestionnaire))
    assert f"Livraison saisie pour le prestataire par {personnes.gestionnaire} : livrée" in page
    assert "En attente d'un autre responsable." in page
    demande = Validation.objects.get(decision=Validation.Decision.EN_ATTENTE)
    client.force_login(personnes.direction)
    client.post(reverse("livraison_decider", args=[demande.pk]), {"decision": "accepter"})
    assert statut(livraison) == StatutLivraison.LIVREE
    assert statut(livraison.commande) == Statut.LIVREE


def test_auteur_ne_decide_pas_sa_saisie(gestionnaire, entreprise, personnes):
    tournee = TourneeFactory(entreprise=entreprise, date=DEMAIN)
    recue(tournee, personnes)
    demarrer(tournee)
    tournee.refresh_from_db()  # partie : relue avant d'être enregistrée
    tournee.chauffeur, tournee.prestataire = None, PrestataireFactory(entreprise=entreprise)
    tournee.save()
    livraison = livraisons(tournee)[0]
    traiter(livraison, par=personnes.gestionnaire)
    demande = Validation.objects.get(decision=Validation.Decision.EN_ATTENTE)
    gestionnaire.post(reverse("livraison_decider", args=[demande.pk]), {"decision": "accepter"})
    assert statut(livraison) == StatutLivraison.EN_ATTENTE
    assert "propre demande" in texte(tableau(gestionnaire))


# --- Ordre des arrêts restants (décision 8.4) ----------------------------------------


def ordre(tournee):
    return [livraison.chargement_id for livraison in livraisons(tournee)]


def test_reordonner_les_arrets_restants(gestionnaire, tournee):
    premiere, seconde, troisieme = livraisons(tournee)
    traiter(premiere)
    contenu = tableau(gestionnaire).content.decode()
    assert f"Monter {premiere.commande.client.nom}" not in contenu
    assert f"Descendre {premiere.commande.client.nom}" not in contenu
    assert f"Monter {seconde.commande.client.nom}" not in contenu  # premier des restants
    assert f"Monter {troisieme.commande.client.nom}" in contenu
    gestionnaire.post(
        reverse("chargement_deplacer", args=[troisieme.chargement_id]), {"sens": "haut"}
    )
    assert ordre(tournee) == [
        premiere.chargement_id,
        troisieme.chargement_id,
        seconde.chargement_id,
    ]


def test_un_arret_traite_garde_sa_place(entreprise, personnes, tournee):
    premiere, seconde, troisieme = livraisons(tournee)
    traiter(premiere)
    with activer_utilisateur(personnes.gestionnaire):
        with pytest.raises(ActionImpossible, match="garde sa place"):
            services.reordonner(
                tournee, [seconde.chargement, premiere.chargement, troisieme.chargement]
            )


def test_tournee_terminee_figee(entreprise, personnes, tournee):
    for livraison in livraisons(tournee):
        traiter(livraison)
    with activer_utilisateur(personnes.gestionnaire):
        with pytest.raises(ActionImpossible, match="partie"):
            services.reordonner(
                tournee, [livraison.chargement for livraison in livraisons(tournee)]
            )


# --- Pastille et droits --------------------------------------------------------------


def test_pastille_anomalies_et_saisies(gestionnaire, personnes, echouee):
    assert alertes.nombre_alertes(personnes.gestionnaire) == 1  # commande en anomalie
    assert 'title="Alertes">1</span>' in tableau(gestionnaire).content.decode()
    gestionnaire.post(
        reverse("livraison_reporter", args=[echouee.pk]), {"date": APRES_DEMAIN.isoformat()}
    )
    assert alertes.nombre_alertes(personnes.gestionnaire) == 0


@pytest.mark.parametrize("role", [Role.COMMERCIAL, Role.CHAUFFEUR])
def test_actions_refusees_hors_ecran_tournees(client, echouee, role):
    client.force_login(utilisateur(echouee, role))
    for nom, donnees in (
        ("livraison_reporter", {"date": APRES_DEMAIN.isoformat()}),
        ("livraison_abandon", {"motif": "x"}),
    ):
        assert client.post(reverse(nom, args=[echouee.pk]), donnees).status_code == 403
    assert statut(echouee) == StatutLivraison.ECHOUEE
