"""Écran Facturation (maquette 15) : livraisons à facturer, choix du document, documents
émis et recherche (§22-23), fiche d'un document, ligne « Facture » de la fiche commande."""

import html
import re

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from clients.tests.factories import AdresseFactory
from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from facturation import alertes
from facturation.models import Facture, StatutFacture
from facturation.tests.test_services import emettre, livree, personne
from noyau.contexte import activer_entreprise

pytestmark = pytest.mark.django_db
R = Role
ECRAN = reverse("ecran_facturation")
EMIS = f"{ECRAN}?vue=emis"


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
def adresse(entreprise):
    return AdresseFactory(client__entreprise=entreprise, client__nom="Restaurant Le Baobab")


def texte(reponse):
    contenu = html.unescape(reponse.content.decode())
    return " ".join(re.sub(r"<[^>]+>", " ", contenu).split())


def connecter(client, entreprise, role):
    utilisateur = personne(entreprise, role)
    client.force_login(utilisateur)
    return utilisateur


# --- Accès ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", [R.DIRECTION, R.GESTIONNAIRE, R.FACTURATION, R.COMPTABILITE])
def test_ecran_ouvert_selon_la_grille(client, entreprise, role):
    connecter(client, entreprise, role)
    reponse = client.get(ECRAN)
    assert reponse.status_code == 200 and "en préparation" not in texte(reponse)


@pytest.mark.parametrize("role", [R.COMMERCIAL, R.CHAUFFEUR, R.PRODUCTION])
def test_ecran_ferme_aux_autres_roles(client, entreprise, role):
    connecter(client, entreprise, role)
    assert client.get(ECRAN).status_code == 403


# --- À facturer et choix du document ---------------------------------------------------


def test_livraisons_a_facturer(client, entreprise, adresse):
    livraison = livree(adresse, livre=6)
    connecter(client, entreprise, R.FACTURATION)
    page = texte(client.get(ECRAN))
    assert "À facturer · 1" in page
    assert f"{livraison.commande.numero} Restaurant Le Baobab 1 500" in page
    assert "Document pour la sélection" in page and "Facture normalisée (FNE)" in page
    assert "Ouvrir la plateforme FNE de la DGI" in page


def test_la_comptabilite_consulte_sans_emettre(client, entreprise, adresse):
    livree(adresse)
    connecter(client, entreprise, R.COMPTABILITE)
    page = texte(client.get(ECRAN))
    assert "À facturer · 1" in page and "Enregistrer le document" not in page


def test_enregistrer_une_fne(client, entreprise, adresse):
    premiere, seconde = livree(adresse), livree(adresse, livre=4)
    auteur = connecter(client, entreprise, R.FACTURATION)
    pdf = SimpleUploadedFile("fne.pdf", b"%PDF-1.4", content_type="application/pdf")
    reponse = client.post(
        reverse("facture_emettre"),
        {
            "livraisons": [premiere.pk, seconde.pk],
            "type_document": "fne",
            "numero_fne": "FNE-2026-000417",
            "echeance": "2026-12-31",
            "fichier": pdf,
        },
    )
    facture = Facture.objects.get()
    assert reponse.url == reverse("facture_detail", args=[facture.pk])
    assert (facture.choix_fait_par, str(facture.echeance)) == (auteur, "2026-12-31")
    page = texte(client.get(reponse.url))
    assert f"Document {facture.numero_interne} enregistré" in page
    assert "Facture normalisée (FNE) n° FNE-2026-000417" in page and "fne.pdf" in page
    assert premiere.commande.numero in page and seconde.commande.numero in page
    assert "À facturer · 0" in texte(client.get(ECRAN))


def test_erreur_du_choix_affichee(client, entreprise, adresse):
    livraison = livree(adresse)
    connecter(client, entreprise, R.FACTURATION)
    reponse = client.post(
        reverse("facture_emettre"), {"livraisons": [livraison.pk], "type_document": "fne"}
    )
    assert reponse.url == ECRAN
    assert "Indiquer le n° de la FNE" in texte(client.get(ECRAN))
    assert not Facture.objects.exists()


def test_emission_refusee_a_la_comptabilite(client, entreprise, adresse):
    livraison = livree(adresse)
    connecter(client, entreprise, R.COMPTABILITE)
    reponse = client.post(
        reverse("facture_emettre"),
        {"livraisons": [livraison.pk], "type_document": "fne", "numero_fne": "FNE-1"},
    )
    assert reponse.status_code == 403
    assert not Facture.objects.exists()


# --- Documents émis et recherche (§22-23) ----------------------------------------------


@pytest.fixture
def documents(entreprise, adresse):
    facturation = personne(entreprise, R.FACTURATION)
    autre = AdresseFactory(client__entreprise=entreprise, client__nom="Café Le Plateau")
    baobab = emettre([livree(adresse)], facturation, numero_fne="FNE-AAA")
    plateau = emettre(
        [livree(autre, livre=4)],
        facturation,
        type_document="autre",
        nature="recu",
        motif="Particulier",
    )
    return baobab, plateau


@pytest.mark.parametrize(
    ("recherche", "baobab_trouve", "plateau_trouve"),
    [
        ({"q": "baobab"}, True, False),
        ({"q": "FNE-AAA"}, True, False),
        ({"montant": "1000"}, False, True),
        ({"etat": "manquant"}, True, True),
        ({"etat": "annulee"}, False, False),
        ({"du": "2000-01-01", "au": "2000-12-31"}, False, False),
    ],
)
def test_recherche_des_documents(
    client, entreprise, documents, recherche, baobab_trouve, plateau_trouve
):
    baobab, plateau = documents
    connecter(client, entreprise, R.COMPTABILITE)
    page = texte(client.get(ECRAN, {"vue": "emis", **recherche}))
    assert (baobab.numero_interne in page) == baobab_trouve
    assert (plateau.numero_interne in page) == plateau_trouve


def test_recherche_par_numero_interne_en_fragment(client, entreprise, documents):
    baobab, plateau = documents
    connecter(client, entreprise, R.GESTIONNAIRE)
    contenu = client.get(
        ECRAN, {"vue": "emis", "q": baobab.numero_interne}, HTTP_HX_REQUEST="true"
    ).content.decode()
    assert contenu.lstrip().startswith('<div id="liste-documents"')
    assert baobab.numero_interne in contenu and plateau.numero_interne not in contenu
    page = texte(client.get(EMIS))
    assert "Reçu" in page and "Document manquant" in page


# --- Fiche d'un document --------------------------------------------------------------


def test_joindre_le_pdf_plus_tard(client, entreprise, documents):
    baobab, _ = documents
    connecter(client, entreprise, R.FACTURATION)
    pdf = SimpleUploadedFile("fne.pdf", b"%PDF-1.4", content_type="application/pdf")
    client.post(reverse("facture_joindre", args=[baobab.pk]), {"fichier": pdf})
    contenu = client.get(reverse("facture_detail", args=[baobab.pk])).content.decode()
    lien = re.search(r'href="([^"]+)">fne\.pdf', contenu).group(1)
    assert client.get(lien).status_code == 200  # lien signé
    assert "Document manquant" not in texte(client.get(reverse("facture_detail", args=[baobab.pk])))


def test_annuler_depuis_la_fiche(client, entreprise, documents):
    baobab, _ = documents
    connecter(client, entreprise, R.FACTURATION)
    assert "Annuler ce document" not in texte(
        client.get(reverse("facture_detail", args=[baobab.pk]))
    )
    connecter(client, entreprise, R.COMPTABILITE)
    page = texte(client.get(reverse("facture_detail", args=[baobab.pk])))
    assert "Annuler ce document" in page and "plateforme de la DGI" in page
    client.post(reverse("facture_annuler", args=[baobab.pk]), {"motif": "Erreur de client"})
    baobab.refresh_from_db()
    assert baobab.statut == StatutFacture.ANNULEE
    assert "Annulé : Erreur de client" in texte(
        client.get(reverse("facture_detail", args=[baobab.pk]))
    )
    assert "À facturer · 1" in texte(client.get(ECRAN))


def test_marquer_a_verifier_depuis_la_fiche(client, entreprise, documents):
    baobab, _ = documents
    connecter(client, entreprise, R.COMPTABILITE)
    client.post(reverse("facture_verifier", args=[baobab.pk]), {"motif": "Quantités contestées"})
    page = texte(client.get(reverse("facture_detail", args=[baobab.pk])))
    assert "Contesté / à vérifier : Quantités contestées" in page
    assert baobab.numero_interne in texte(client.get(ECRAN, {"vue": "emis", "etat": "a_verifier"}))


def test_actions_refusees_aux_autres_roles(client, entreprise, documents):
    baobab, _ = documents
    connecter(client, entreprise, R.COMMERCIAL)
    assert client.get(reverse("facture_detail", args=[baobab.pk])).status_code == 403
    assert (
        client.post(reverse("facture_annuler", args=[baobab.pk]), {"motif": "x"}).status_code == 403
    )


# --- Fiche commande, pastille, isolation ------------------------------------------------


def test_ligne_facture_de_la_fiche_commande(client, entreprise, documents):
    baobab, _ = documents
    commande = baobab.lignes.get().livraison.commande
    connecter(client, entreprise, R.GESTIONNAIRE)
    contenu = client.get(reverse("commande_detail", args=[commande.pk])).content.decode()
    assert reverse("facture_detail", args=[baobab.pk]) in contenu
    assert f"{baobab.numero_interne} · FNE" in texte(
        client.get(reverse("commande_detail", args=[commande.pk]))
    )


def test_pastille_du_menu(client, entreprise, adresse, documents):
    livree(adresse)  # une livraison encore à facturer
    utilisateur = connecter(client, entreprise, R.FACTURATION)
    assert alertes.nombre_alertes(utilisateur) == 3  # 1 à facturer + 2 documents manquants
    assert 'title="Alertes">3</span>' in client.get(ECRAN).content.decode()


def test_isolation_entre_entreprises(client, documents):
    baobab, _ = documents
    with activer_entreprise(None):
        etranger = UtilisateurFactory(entreprise=EntrepriseFactory(), roles=[R.FACTURATION])
    client.force_login(etranger)
    assert client.get(reverse("facture_detail", args=[baobab.pk])).status_code == 404
    page = texte(client.get(EMIS))
    assert baobab.numero_interne not in page and "À facturer · 0" in page
