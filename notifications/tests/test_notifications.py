"""Notifications dans l'application (§16.1 ; décision 8.5) : demandes à décider, échecs,
retours, incidents, plan modifié, tâches planifiées, page et cloche, isolation."""

import datetime
import html

import pytest
from django.urls import reverse

from clients.tests.factories import ClientFactory
from commandes import services as commandes
from commandes.models import ModeleRecurrent, Statut
from commandes.tests.factories import CommandeFactory
from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from logistique import services as logistique
from logistique.models import EtatRetour, Livraison, MotifEchec, TypeIncident
from logistique.services import Resultat
from logistique.tests.factories import TourneeFactory
from logistique.tests.test_depart import demarrer, recue
from logistique.tests.test_services_tournee import DEMAIN, forcer
from notifications.models import Notification, TypeNotification
from notifications.tasks import notifications_planifiees
from noyau.contexte import activer_entreprise, activer_utilisateur
from noyau.templatetags.format_fr import ESPACE_FINE
from paiements.tests.test_services import facture
from production import services as production
from production.models import PlanProduction, StatutPlan

pytestmark = pytest.mark.django_db
R = Role
AUJOURD_HUI = datetime.date.today()


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
def personnes(entreprise):
    def creer(role):
        return UtilisateurFactory(entreprise=entreprise, roles=[role])

    class Personnes:
        gestionnaire = creer(R.GESTIONNAIRE)
        direction = creer(R.DIRECTION)
        preparateur = creer(R.PREPARATION)
        production = creer(R.PRODUCTION)
        comptable = creer(R.COMPTABILITE)
        commercial = creer(R.COMMERCIAL)
        autre_chauffeur = creer(R.CHAUFFEUR)

    return Personnes


def recues_par(personne, type=None):
    notifications = Notification.objects.filter(destinataire=personne)
    return list(notifications.filter(type=type) if type else notifications)


# --- Demandes à décider (toutes les validations) ---------------------------------------


def test_demande_d_annulation_previent_les_autres_responsables(entreprise, personnes):
    commande = forcer(CommandeFactory(adresse__client__entreprise=entreprise), Statut.CONFIRMEE)
    with activer_utilisateur(personnes.gestionnaire):
        commandes.demander_annulation(commande, "Client fermé")
    (notification,) = recues_par(personnes.direction)
    assert notification.type == TypeNotification.DEMANDE
    assert notification.texte == (
        f"Demande d'annulation · {commande.numero} · {commande.client.nom} : « Client fermé »"
    )
    assert notification.lien == reverse("commande_detail", args=[commande.pk])
    assert notification.cree_par == personnes.gestionnaire
    assert not recues_par(personnes.gestionnaire)  # pas l'auteur
    assert not recues_par(personnes.commercial) and not recues_par(personnes.comptable)


def test_demande_refusee_ne_previent_personne(entreprise, personnes):
    commande = forcer(CommandeFactory(adresse__client__entreprise=entreprise), Statut.BROUILLON)
    with activer_utilisateur(personnes.gestionnaire):
        with pytest.raises(commandes.ActionImpossible):
            commandes.demander_annulation(commande, "x")
    assert not Notification.objects.exists()


def test_ecart_au_chargement(entreprise, personnes):
    from logistique.models import MotifEcart
    from logistique.tests.test_services_controle import affecter, compter

    tournee = TourneeFactory(entreprise=entreprise, date=DEMAIN)
    chargement = affecter(tournee, personnes)
    compter(chargement, [30, 20], personnes.preparateur, motif=MotifEcart.PRODUIT_ABIME)
    with activer_utilisateur(personnes.preparateur):
        logistique.signaler_ecart(chargement, "Fournée ratée")
    (notification,) = recues_par(personnes.gestionnaire)
    assert notification.texte.startswith("Exception à décider · ")
    assert notification.lien == f"{reverse('ecran_tournees')}?date={DEMAIN:%Y-%m-%d}"
    assert recues_par(personnes.direction)


# --- Livraison, retour, incident -------------------------------------------------------


@pytest.fixture
def livraison(entreprise, personnes):
    tournee = TourneeFactory(entreprise=entreprise, date=DEMAIN)
    chargement = recue(tournee, personnes)
    demarrer(tournee)
    return Livraison.objects.get(chargement=chargement)


def test_echec_de_livraison(personnes, livraison):
    with activer_utilisateur(livraison.tournee.chauffeur):
        logistique.cloturer_livraison(livraison, Resultat.ECHEC, motif=MotifEchec.CLIENT_ABSENT)
    (notification,) = recues_par(personnes.gestionnaire, TypeNotification.LIVRAISON)
    assert notification.texte == (
        f"Échec · {livraison.commande.numero} · {livraison.commande.client.nom} : client absent"
    )
    assert not recues_par(personnes.direction, TypeNotification.LIVRAISON)


def test_livraison_complete_sans_notification(personnes, livraison):
    with activer_utilisateur(livraison.tournee.chauffeur):
        quantites = dict(zip(livraison.lignes.order_by("pk"), [60, 20], strict=True))
        logistique.cloturer_livraison(
            livraison, Resultat.LIVREE, quantites=quantites, receptionnaire_nom="Mme Bamba"
        )
    assert not recues_par(personnes.gestionnaire, TypeNotification.LIVRAISON)


def test_retour_et_incident(personnes, livraison):
    chauffeur = livraison.tournee.chauffeur
    with activer_utilisateur(chauffeur):
        logistique.signaler_incident(livraison.tournee, TypeIncident.PANNE, "Pneu crevé")
        logistique.cloturer_livraison(livraison, Resultat.ECHEC, motif=MotifEchec.REFUS)
        produit = livraison.lignes.order_by("pk").first().ligne_commande.produit
        logistique.declarer_retour(
            livraison, produit=produit, quantite=5, motif="Écrasés", etat=EtatRetour.ABIME
        )
    (retour,) = recues_par(personnes.direction, TypeNotification.RETOUR)
    assert retour.texte.startswith(f"Retour à décider · 5 {produit.nom} ·")
    (incident,) = recues_par(personnes.gestionnaire, TypeNotification.INCIDENT)
    assert incident.texte == f"Incident · {livraison.tournee.numero} · panne : Pneu crevé"
    assert not recues_par(chauffeur)


# --- Production : plan modifié après validation ---------------------------------------


def test_commande_ajoutee_a_un_plan_valide(entreprise, personnes):
    PlanProduction.objects.create(
        entreprise=entreprise, date_livraison=DEMAIN, statut=StatutPlan.VALIDE
    )
    commande = forcer(
        CommandeFactory(adresse__client__entreprise=entreprise, date_livraison=DEMAIN),
        Statut.CONFIRMEE,
    )
    with activer_utilisateur(personnes.gestionnaire):
        production.integrer_commande(commande)
    (notification,) = recues_par(personnes.production, TypeNotification.PRODUCTION)
    assert notification.texte == (
        f"Plan du {DEMAIN:%d/%m} modifié après validation : {commande.numero} ajoutée"
    )


# --- Tâches planifiées ---------------------------------------------------------------


def a(heure):
    return datetime.datetime.combine(AUJOURD_HUI, datetime.time.fromisoformat(heure)).isoformat()


def test_resume_du_jour_une_seule_fois(entreprise, personnes):
    client = ClientFactory(entreprise=entreprise)
    facture(client, 50000, echeance_dans=-3)
    notifications_planifiees(a("06:45"))
    assert not Notification.objects.exists()
    notifications_planifiees(a("07:00"))
    notifications_planifiees(a("07:15"))
    (resume,) = recues_par(personnes.direction, TypeNotification.RESUME)
    assert resume.texte.startswith("Résumé du jour : 0 commande à livrer (0 à confirmer)")
    assert f"1 facture en retard (50{ESPACE_FINE}000 FCFA)" in resume.texte
    assert recues_par(personnes.gestionnaire, TypeNotification.RESUME)
    (retard,) = recues_par(personnes.comptable, TypeNotification.PAIEMENT)
    assert retard.lien == reverse("ecran_paiements")
    assert not recues_par(personnes.commercial)


def test_recurrentes_non_confirmees_a_l_heure_limite(entreprise, personnes):
    demain = AUJOURD_HUI + datetime.timedelta(days=1)
    commande = forcer(
        CommandeFactory(adresse__client__entreprise=entreprise, date_livraison=demain),
        Statut.A_CONFIRMER,
    )
    modele = ModeleRecurrent.objects.create(
        client=commande.client,
        adresse=commande.adresse,
        jours_semaine=[demain.weekday()],
        creneau="06h–08h",
        date_debut=AUJOURD_HUI,
    )
    type(commande).toutes_entreprises.filter(pk=commande.pk).update(modele_recurrent=modele)
    notifications_planifiees(a("19:45"))
    assert not recues_par(personnes.commercial, TypeNotification.RECURRENTES)
    notifications_planifiees(a("20:00"))
    (alerte,) = recues_par(personnes.commercial, TypeNotification.RECURRENTES)
    assert alerte.texte == (
        f"1 commande récurrente du {demain:%d/%m} toujours à confirmer après l'heure limite"
    )


# --- Page, cloche, isolation ---------------------------------------------------------


@pytest.fixture
def boite(entreprise, personnes):
    """Deux notifications pour la Direction : une ancienne lue, une récente non lue."""
    lue = Notification.objects.create(
        destinataire=personnes.direction,
        type=TypeNotification.RESUME,
        texte="Résumé d'hier",
        lien=reverse("ecran_tableau_de_bord"),
    )
    lue.lue_le = lue.cree_le
    lue.save()
    non_lue = Notification.objects.create(
        destinataire=personnes.direction,
        type=TypeNotification.DEMANDE,
        texte="Demande d'annulation · CMD-1",
        lien=reverse("ecran_commandes"),
    )
    return lue, non_lue


def test_cloche_et_page(client, personnes, boite):
    lue, non_lue = boite
    client.force_login(personnes.direction)
    page = html.unescape(client.get(reverse("notifications")).content.decode())
    assert 'title="Non lues">1</span>' in page
    assert page.index(non_lue.texte) < page.index(lue.texte)  # non lues d'abord
    reponse = client.post(reverse("notification_ouvrir", args=[non_lue.pk]))
    assert reponse.url == reverse("ecran_commandes")
    non_lue.refresh_from_db()
    assert non_lue.lue_le is not None
    assert 'title="Non lues"' not in client.get(reverse("notifications")).content.decode()


def test_tout_marquer_comme_lu(client, personnes, boite):
    client.force_login(personnes.direction)
    client.post(reverse("notifications_tout_lire"))
    assert not Notification.objects.filter(lue_le__isnull=True).exists()


def test_lien_exterieur_ignore(client, personnes):
    notification = Notification.objects.create(
        destinataire=personnes.direction, type="demande", texte="x", lien="https://exemple.com/"
    )
    client.force_login(personnes.direction)
    reponse = client.post(reverse("notification_ouvrir", args=[notification.pk]))
    assert reponse.url == reverse("notifications")


def test_notification_d_un_autre_introuvable(client, personnes, boite):
    _, non_lue = boite
    client.force_login(personnes.gestionnaire)
    assert client.post(reverse("notification_ouvrir", args=[non_lue.pk])).status_code == 404
    assert "CMD-1" not in client.get(reverse("notifications")).content.decode()


def test_isolation_entre_entreprises(client, boite):
    with activer_entreprise(None):
        etranger = UtilisateurFactory(entreprise=EntrepriseFactory(), roles=[R.DIRECTION])
    with activer_entreprise(etranger.entreprise):
        assert not Notification.objects.exists()
    client.force_login(etranger)
    assert "Aucune notification." in client.get(reverse("notifications")).content.decode()
