"""Tableau de bord (maquette 8, §16.2 ; décision 8.5) : chiffres justes sur un jeu connu,
contenu selon le rôle, export, isolation."""

import datetime
import html
import io
import re
from decimal import Decimal

import pytest
from django.urls import reverse
from openpyxl import load_workbook

from clients.tests.factories import ClientFactory
from commandes.models import ModeleRecurrent, Statut
from commandes.tests.factories import CommandeFactory
from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from logistique.models import StatutTournee
from logistique.tests.test_services_tournee import forcer
from noyau.contexte import activer_entreprise, activer_utilisateur
from noyau.models import Validation
from noyau.services import demander_validation
from noyau.templatetags.format_fr import ESPACE_FINE, fcfa_court
from paiements.tests.test_services import facture
from production.models import StatutPlan
from production.tests.factories import BesoinProduitFactory

pytestmark = pytest.mark.django_db
R = Role
AUJOURD_HUI = datetime.date.today()
ECRAN = reverse("ecran_tableau_de_bord")


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


def texte(reponse):
    contenu = html.unescape(reponse.content.decode())
    return " ".join(re.sub(r"<[^>]+>", " ", contenu).split())


def personne(entreprise, role):
    return UtilisateurFactory(entreprise=entreprise, roles=[role])


@pytest.fixture
def commercial(entreprise):
    return personne(entreprise, R.COMMERCIAL)


@pytest.fixture
def journee(entreprise, commercial):
    """Commandes du jour : 2 à confirmer (dont 1 récurrente), 1 en préparation, 1 livrée,
    1 partielle, 1 en anomalie (créée par le commercial), 1 brouillon et 1 annulée
    (non comptées) ; une commande de demain."""

    def commande(statut, **kwargs):
        return forcer(
            CommandeFactory(
                adresse__client__entreprise=entreprise, date_livraison=AUJOURD_HUI, **kwargs
            ),
            statut,
        )

    recurrente = commande(Statut.A_CONFIRMER)
    modele = ModeleRecurrent.objects.create(
        client=recurrente.client,
        adresse=recurrente.adresse,
        jours_semaine=[0],
        creneau="06h–08h",
        date_debut=AUJOURD_HUI,
    )
    type(recurrente).toutes_entreprises.filter(pk=recurrente.pk).update(modele_recurrent=modele)
    commande(Statut.A_CONFIRMER)
    commande(Statut.EN_PREPARATION)
    commande(Statut.LIVREE)
    commande(Statut.PARTIELLEMENT_LIVREE)
    anomalie = commande(Statut.EN_ANOMALIE, cree_par=commercial)
    commande(Statut.BROUILLON)
    commande(Statut.ANNULEE)
    forcer(CommandeFactory(adresse__client__entreprise=entreprise), Statut.A_CONFIRMER)  # demain
    return anomalie


# --- Chiffres --------------------------------------------------------------------------


def test_cartes_et_avancement(client, entreprise, journee):
    client.force_login(personne(entreprise, R.GESTIONNAIRE))
    page = texte(client.get(ECRAN))
    assert "Commandes du jour 6 dont 1 récurrente" in page
    assert "Livrées 2 1 partielle" in page
    assert "En anomalie 1 Traiter" in page
    assert "À confirmer 2 En préparation 1 En livraison 0 Livrées 2 En anomalie 1" in page


def test_tournees_en_cours(client, entreprise):
    from logistique.tests.factories import TourneeFactory

    tournee = forcer(
        TourneeFactory(entreprise=entreprise, date=AUJOURD_HUI), StatutTournee.EN_COURS
    )
    client.force_login(personne(entreprise, R.DIRECTION))
    page = texte(client.get(ECRAN))
    assert f"Tournées en cours {tournee.numero} · {tournee.chauffeur} 0 / 0" in page


def test_alertes_a_traiter(client, entreprise, journee):
    BesoinProduitFactory(
        entreprise=entreprise,
        plan__entreprise=entreprise,
        plan__date_livraison=AUJOURD_HUI,
        plan__statut=StatutPlan.VALIDE,
        produit__nom="Pain de mie",
        quantite_a_produire=90,
        quantite_produite=0,
    )
    gestionnaire, direction = (
        personne(entreprise, R.GESTIONNAIRE),
        personne(entreprise, R.DIRECTION),
    )
    with activer_utilisateur(gestionnaire):
        for _ in range(2):
            demander_validation(
                CommandeFactory(adresse__client__entreprise=entreprise),
                Validation.Action.ANNULATION,
                motif="x",
            )
    client.force_login(direction)
    page = texte(client.get(ECRAN))
    assert f"Écart Pain de mie : 90 manquent pour la livraison du {AUJOURD_HUI:%d/%m}" in page
    assert f"Échec {journee.numero} · {journee.client.nom}" in page
    assert "Validation 2 demandes d'annulation attendent votre décision" in page
    # L'auteur des demandes ne les voit pas comme « à décider » (décision 8.1).
    client.force_login(gestionnaire)
    assert "attendent votre décision" not in texte(client.get(ECRAN))


def test_creances_echues(client, entreprise):
    hotel = ClientFactory(entreprise=entreprise, nom="Hôtel Ivoire Lagune")
    baobab = ClientFactory(entreprise=entreprise, nom="Restaurant Le Baobab")
    facture(hotel, 2_000_000, -5)
    facture(hotel, 400_000, -40)
    facture(baobab, 415_500, -2)
    facture(baobab, 900_000, 10)  # non échue
    client.force_login(personne(entreprise, R.COMPTABILITE))
    page = texte(client.get(ECRAN))
    assert "Créances échues 2,8 M FCFA · 2 clients" in page
    assert (
        "Plus fortes créances échues Hôtel Ivoire Lagune 2 400 000 Restaurant Le Baobab 415 500"
        in page
    )


@pytest.mark.parametrize(
    ("valeur", "attendu"),
    [(Decimal(415500), "415 500"), (Decimal(2_400_000), "2,4 M"), (Decimal(3_000_000), "3 M")],
)
def test_montant_court(valeur, attendu):
    assert fcfa_court(valeur) == attendu.replace(" ", ESPACE_FINE)


# --- Contenu selon le rôle (décision 8.5) -----------------------------------------------


def test_le_commercial_ne_voit_que_ses_commandes(client, commercial, journee):
    client.force_login(commercial)
    page = texte(client.get(ECRAN))
    assert "mes commandes" in page
    assert "Commandes du jour 1" in page and "En anomalie 1" in page
    assert "Créances échues" not in page and "Tournées en cours" not in page


def test_la_comptabilite_sans_suivi_logistique(client, entreprise, journee):
    client.force_login(personne(entreprise, R.COMPTABILITE))
    page = texte(client.get(ECRAN))
    assert "Créances échues" in page and "Tournées en cours" not in page


@pytest.mark.parametrize("role", [R.PRODUCTION, R.CHAUFFEUR, R.FACTURATION])
def test_ecran_ferme_aux_autres_roles(client, entreprise, role):
    client.force_login(personne(entreprise, role))
    assert client.get(ECRAN).status_code == 403
    assert client.get(reverse("tableau_de_bord_export")).status_code == 403


def test_arrivee_de_la_direction_sur_le_tableau_de_bord(client, entreprise):
    client.force_login(personne(entreprise, R.DIRECTION))
    reponse = client.get(reverse("accueil"))
    assert reponse.url == ECRAN


# --- Export et autre date ---------------------------------------------------------------


def test_export_excel(client, entreprise, journee):
    client.force_login(personne(entreprise, R.GESTIONNAIRE))
    reponse = client.get(reverse("tableau_de_bord_export"), {"date": AUJOURD_HUI.isoformat()})
    document = load_workbook(io.BytesIO(reponse.content))
    synthese = dict(list(document["Synthèse"].values)[1:])
    assert synthese["Commandes du jour"] == 6 and synthese["En anomalie (toutes dates)"] == 1
    assert len(list(document["Commandes"].values)) == 7  # entête + 6 commandes du jour


def test_autre_date(client, entreprise, journee):
    client.force_login(personne(entreprise, R.GESTIONNAIRE))
    demain = AUJOURD_HUI + datetime.timedelta(days=1)
    page = texte(client.get(ECRAN, {"date": demain.isoformat()}))
    assert "Commandes du jour 1" in page and "Aujourd'hui" in page


def test_isolation_entre_entreprises(client, journee):
    with activer_entreprise(None):
        etranger = UtilisateurFactory(entreprise=EntrepriseFactory(), roles=[R.DIRECTION])
    client.force_login(etranger)
    page = texte(client.get(ECRAN))
    assert "Commandes du jour 0" in page and "En anomalie 0" in page
