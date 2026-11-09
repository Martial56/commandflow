"""Recette du Sprint 6 (§22-22, §22-23, §22-24, §22-26) : de la livraison au paiement, par
les écrans, chaque acteur avec son propre compte."""

import html
import io
import re

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from openpyxl import load_workbook

from clients.tests.factories import AdresseFactory
from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from facturation.models import Facture
from facturation.tests.test_services import livree
from logistique.models import Livraison, StatutLivraison
from noyau.contexte import activer_entreprise
from paiements.models import Paiement

pytestmark = pytest.mark.django_db
R = Role


@pytest.fixture(autouse=True)
def sans_2fa_obligatoire(settings):
    settings.MIDDLEWARE = [
        m for m in settings.MIDDLEWARE if "DoubleAuthentificationObligatoire" not in m
    ]


@pytest.fixture
def entreprise():
    return EntrepriseFactory()


@pytest.fixture
def acteurs(entreprise):
    roles = {"mariam": R.FACTURATION, "serge": R.COMPTABILITE, "ibrahim": R.DIRECTION}
    return {nom: UtilisateurFactory(entreprise=entreprise, roles=[r]) for nom, r in roles.items()}


def texte(reponse):
    contenu = html.unescape(reponse.content.decode())
    return " ".join(re.sub(r"<[^>]+>", " ", contenu).split())


def connecter(client, personne):
    client.force_login(personne)
    return client


def test_parcours_de_la_livraison_au_paiement(client, entreprise, acteurs):
    with activer_entreprise(entreprise):
        baobab = AdresseFactory(client__entreprise=entreprise, client__nom="Restaurant Le Baobab")
        plateau = AdresseFactory(client__entreprise=entreprise, client__nom="Café Le Plateau")
        premiere, seconde = livree(baobab, livre=10), livree(baobab, livre=8)  # 2 500 + 2 000
        recu = livree(plateau, livre=4)  # 1 000

    # 1. §22-22 : Mariam choisit la FNE pour les deux livraisons du Baobab.
    connecter(client, acteurs["mariam"])
    assert "À facturer · 3" in texte(client.get(reverse("ecran_facturation")))
    pdf = SimpleUploadedFile("fne-0417.pdf", b"%PDF-1.4", content_type="application/pdf")
    client.post(
        reverse("facture_emettre"),
        {
            "livraisons": [premiere.pk, seconde.pk],
            "type_document": "fne",
            "numero_fne": "FNE-2026-000417",
            "fichier": pdf,
        },
    )
    fne = Facture.toutes_entreprises.get(numero_fne="FNE-2026-000417")
    assert (fne.montant_ttc, fne.choix_fait_par) == (4500, acteurs["mariam"])
    # La livraison reste enregistrée indépendamment de ce choix.
    for livraison in (premiere, seconde):
        assert Livraison.toutes_entreprises.get(pk=livraison.pk).statut == StatutLivraison.LIVREE
    # Autre document pour le Café : un reçu, avec son motif.
    client.post(
        reverse("facture_emettre"),
        {
            "livraisons": [recu.pk],
            "type_document": "autre",
            "nature": "recu",
            "motif": "Réglé comptant à la livraison",
        },
    )
    document_recu = Facture.toutes_entreprises.get(client__nom="Café Le Plateau")
    assert "À facturer · 0" in texte(client.get(reverse("ecran_facturation")))

    # 2. §22-23 : chaque document se retrouve par client, numéro, date et montant.
    connecter(client, acteurs["serge"])
    for recherche, attendu, absent in (
        ({"q": "baobab"}, fne, document_recu),
        ({"q": fne.numero_interne}, fne, document_recu),
        ({"q": "FNE-2026-000417"}, fne, document_recu),
        ({"montant": "1000"}, document_recu, fne),
        ({"du": fne.date_document.isoformat(), "au": fne.date_document.isoformat()}, fne, None),
    ):
        page = texte(client.get(reverse("ecran_facturation"), {"vue": "emis", **recherche}))
        assert attendu.numero_interne in page, recherche
        if absent:
            assert absent.numero_interne not in page, recherche

    # 3. §22-24 : un paiement partiel met à jour le solde sans marquer la facture payée.
    client.post(
        reverse("paiement_enregistrer"),
        {
            "client": fne.client_id,
            "montant": "2000",
            "mode": "mobile_money",
            "reference": "MM-1",
            f"affecte-{fne.pk}": "2000",
        },
    )
    releve = texte(client.get(reverse("releve_client", args=[fne.client_id])))
    assert "Reste dû 2 500" in releve and "Partiellement payé" in releve
    assert " Payé " not in f" {releve} "
    classeur = load_workbook(io.BytesIO(client.get(reverse("creances_excel")).content))
    assert ("Restaurant Le Baobab", 2500, 0, 0, 2500) in list(
        classeur["Créances par client"].values
    )
    client.post(
        reverse("paiement_enregistrer"),
        {
            "client": fne.client_id,
            "montant": "2500",
            "mode": "especes",
            f"affecte-{fne.pk}": "2500",
        },
    )
    releve = texte(client.get(reverse("releve_client", args=[fne.client_id])))
    assert "Reste dû 0" in releve and "Payé" in releve
    assert Paiement.toutes_entreprises.filter(client_id=fne.client_id).count() == 2

    # La Direction voit le document, sans alerte « document manquant » (PDF joint).
    connecter(client, acteurs["ibrahim"])
    fiche = texte(client.get(reverse("facture_detail", args=[fne.pk])))
    assert "fne-0417.pdf" in fiche and "Document manquant" not in fiche

    # 4. §22-26 : rien n'est accessible depuis le compte d'une autre entreprise.
    with activer_entreprise(None):
        etranger = UtilisateurFactory(entreprise=EntrepriseFactory(), roles=[R.DIRECTION])
    connecter(client, etranger)
    assert client.get(reverse("facture_detail", args=[fne.pk])).status_code == 404
    assert client.get(reverse("releve_client", args=[fne.client_id])).status_code == 404
    assert client.get(reverse("releve_client_pdf", args=[fne.client_id])).status_code == 404
    page = texte(client.get(reverse("ecran_facturation"), {"vue": "emis"}))
    assert fne.numero_interne not in page and document_recu.numero_interne not in page
    assert "Restaurant Le Baobab" not in texte(client.get(reverse("ecran_paiements")))
    assert "Commandes du jour 0" in texte(client.get(reverse("ecran_tableau_de_bord")))
    classeur = load_workbook(io.BytesIO(client.get(reverse("creances_excel")).content))
    assert len(list(classeur["Factures ouvertes"].values)) == 1  # entête seule
