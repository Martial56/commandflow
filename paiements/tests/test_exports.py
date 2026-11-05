"""Exports Excel et PDF (§15, §16.2) : créances, paiements, relevé client, documents émis,
fiche atelier ; droits et isolation."""

import datetime
import io
import re

import pytest
from django.urls import reverse
from openpyxl import load_workbook

from clients.tests.factories import ClientFactory
from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from noyau import exports
from noyau.contexte import activer_entreprise, activer_utilisateur
from paiements import services
from paiements.tests.test_services import facture, payer
from production.models import BesoinProduit, PlanProduction

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
def comptable(entreprise):
    return UtilisateurFactory(entreprise=entreprise, roles=[R.COMPTABILITE])


@pytest.fixture
def creances(entreprise, comptable):
    """Hôtel : 372 000 non échu et 640 000 à 12 jours ; Baobab : 102 000 réglés à moitié."""
    hotel = ClientFactory(entreprise=entreprise, nom="Hôtel Ivoire Lagune")
    baobab = ClientFactory(entreprise=entreprise, nom="Restaurant Le Baobab")
    facture(hotel, 372000, 5)
    facture(hotel, 640000, -12)
    partielle = facture(baobab, 102000, -40)
    payer(baobab, 51000, comptable, {partielle: 51000}, reference="MM-1")
    return hotel, baobab, partielle


@pytest.fixture
def html_pdf(monkeypatch):
    """HTML envoyé à WeasyPrint, pour en vérifier le contenu."""
    rendus = []
    rendre = exports.render_to_string

    def espion(*args, **kwargs):
        rendus.append(rendre(*args, **kwargs))
        return rendus[-1]

    monkeypatch.setattr(exports, "render_to_string", espion)
    return rendus


def classeur(reponse):
    assert reponse["Content-Type"] == exports.TYPE_EXCEL
    assert "attachment" in reponse["Content-Disposition"]
    return load_workbook(io.BytesIO(reponse.content))


def texte(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


# --- Créances -------------------------------------------------------------------------


def test_creances_en_excel(client, comptable, creances):
    client.force_login(comptable)
    document = classeur(client.get(reverse("creances_excel")))
    assert document.sheetnames == ["Créances par client", "Factures ouvertes"]
    lignes = list(document["Créances par client"].values)
    assert lignes[0] == ("Client", "Non échu", "1–30 j", "+30 j", "Total")
    assert lignes[1] == ("Hôtel Ivoire Lagune", 372000, 640000, 0, 1012000)
    assert lignes[2] == ("Restaurant Le Baobab", 0, 0, 51000, 51000)
    assert document["Créances par client"]["E2"].number_format == exports.FORMAT_FCFA
    factures = list(document["Factures ouvertes"].values)
    baobab = next(ligne for ligne in factures if ligne[1] == "Restaurant Le Baobab")
    assert baobab[4:9] == (102000, 51000, 51000, 40, "En retard")


def test_creances_en_pdf(client, comptable, creances, html_pdf):
    client.force_login(comptable)
    reponse = client.get(reverse("creances_pdf"))
    assert reponse["Content-Type"] == "application/pdf" and reponse.content.startswith(b"%PDF")
    page = texte(html_pdf[-1])
    assert "État des créances" in page
    assert "Hôtel Ivoire Lagune 372 000 640 000 0 1 012 000" in page
    assert "Total 372 000 640 000 51 000 1 063 000" in page


# --- Paiements de la période --------------------------------------------------------


def test_paiements_de_la_periode(client, comptable, creances):
    hotel, baobab, _ = creances
    ancien = payer(hotel, 1000, comptable, date=AUJOURD_HUI - datetime.timedelta(days=60))
    annule = payer(hotel, 2000, comptable)
    with activer_utilisateur(comptable):
        services.annuler_paiement(annule, "Chèque rejeté")
    client.force_login(comptable)
    debut = (AUJOURD_HUI - datetime.timedelta(days=1)).isoformat()
    document = classeur(client.get(reverse("paiements_excel"), {"du": debut}))
    lignes = list(document["Paiements"].values)[1:]
    montants = {ligne[4] for ligne in lignes}
    assert montants == {51000, 2000} and ancien.montant not in montants
    rejete = next(ligne for ligne in lignes if ligne[4] == 2000)
    assert rejete[8:10] == ("oui", "Chèque rejeté")
    partiel = next(ligne for ligne in lignes if ligne[4] == 51000)
    assert partiel[1] == "Restaurant Le Baobab" and partiel[3] == "MM-1"


# --- Relevé client ---------------------------------------------------------------------


def test_releve_en_pdf(client, comptable, creances, html_pdf):
    _, baobab, partielle = creances
    client.force_login(comptable)
    reponse = client.get(reverse("releve_client_pdf", args=[baobab.pk]))
    assert reponse.content.startswith(b"%PDF")
    assert f"releve-{baobab.code}" in reponse["Content-Disposition"]
    page = texte(html_pdf[-1])
    assert "Relevé de compte · Restaurant Le Baobab" in page
    assert "Reste dû : 51 000 FCFA" in page
    assert f"51 000 → {partielle.numero_interne}" in page


# --- Documents émis et fiche atelier ---------------------------------------------------


def test_documents_emis_selon_la_recherche(client, entreprise, creances):
    hotel, _, partielle = creances
    client.force_login(UtilisateurFactory(entreprise=entreprise, roles=[R.FACTURATION]))
    document = classeur(client.get(reverse("factures_excel"), {"q": "baobab"}))
    lignes = list(document["Documents émis"].values)
    assert len(lignes) == 2  # entête + la facture du Baobab
    assert lignes[1][0] == partielle.numero_interne
    assert lignes[1][8:11] == (51000, "En retard", "Émise")


def test_fiche_atelier_en_pdf(client, entreprise, html_pdf):
    from catalogue.tests.factories import ProduitFactory

    plan = PlanProduction.objects.create(entreprise=entreprise, date_livraison=AUJOURD_HUI)
    produit = ProduitFactory(entreprise=entreprise, nom="Pain baguette")
    BesoinProduit.objects.create(
        entreprise=entreprise, plan=plan, produit=produit, quantite_a_produire=320
    )
    client.force_login(UtilisateurFactory(entreprise=entreprise, roles=[R.PRODUCTION]))
    reponse = client.get(reverse("production_fiche_atelier_pdf", args=[plan.pk]))
    assert reponse.content.startswith(b"%PDF")
    assert "Pain baguette 320" in texte(html_pdf[-1])
    page = client.get(reverse("production_fiche_atelier", args=[plan.pk])).content.decode()
    assert reverse("production_fiche_atelier_pdf", args=[plan.pk]) in page


# --- Droits et isolation ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "nom"),
    [
        (R.FACTURATION, "creances_excel"),
        (R.COMMERCIAL, "creances_pdf"),
        (R.CHAUFFEUR, "paiements_excel"),
        (R.COMMERCIAL, "factures_excel"),
    ],
)
def test_exports_refuses(client, entreprise, role, nom):
    client.force_login(UtilisateurFactory(entreprise=entreprise, roles=[role]))
    assert client.get(reverse(nom)).status_code == 403


def test_isolation_entre_entreprises(client, creances):
    _, baobab, _ = creances
    with activer_entreprise(None):
        etranger = UtilisateurFactory(entreprise=EntrepriseFactory(), roles=[R.COMPTABILITE])
    client.force_login(etranger)
    document = classeur(client.get(reverse("creances_excel")))
    assert list(document["Créances par client"].values) == [
        ("Client", "Non échu", "1–30 j", "+30 j", "Total")
    ]
    assert client.get(reverse("releve_client_pdf", args=[baobab.pk])).status_code == 404
