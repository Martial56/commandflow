"""Recette §22-22 à 24 dans le navigateur : la facturation choisit FNE ou autre document
pour des livraisons d'un même client, la comptabilité retrouve chaque document par
recherche, puis enregistre des paiements répartis automatiquement sur les factures."""

import re

import pytest
from django.urls import reverse
from playwright.sync_api import expect

from clients.tests.factories import AdresseFactory
from comptes.models import Role
from facturation.models import Facture
from facturation.tests.test_services import livree
from logistique.models import Livraison, StatutLivraison
from noyau.contexte import activer_entreprise
from paiements.models import Paiement

pytestmark = pytest.mark.recette
PDF_FNE = {"name": "fne-0417.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4\n%%EOF"}


@pytest.fixture
def livraisons(entreprise):
    """Deux livraisons du Baobab (2 500 et 2 000 FCFA), une du Café (1 000 FCFA)."""
    with activer_entreprise(entreprise):
        baobab = AdresseFactory(client__entreprise=entreprise, client__nom="Restaurant Le Baobab")
        plateau = AdresseFactory(client__entreprise=entreprise, client__nom="Café Le Plateau")
        return {
            "baobab": [livree(baobab, livre=10), livree(baobab, livre=8)],
            "plateau": livree(plateau, livre=4),
        }


def case(page, livraison):
    return page.get_by_label(f"Sélectionner {livraison.commande.numero}")


def test_22_22_23_24_de_la_livraison_au_paiement(ouvrir, personne, livraisons):
    premiere, seconde = livraisons["baobab"]
    recu = livraisons["plateau"]

    # 1. §22-22 : la facturation coche les deux livraisons du Baobab ; celles d'un autre
    # client ne sont plus sélectionnables. Elle archive la FNE émise sur le portail DGI.
    mariam = ouvrir(personne(Role.FACTURATION, first_name="Mariam", last_name="O."))
    mariam.goto(reverse("ecran_facturation"))
    expect(mariam.get_by_role("link", name="À facturer · 3")).to_be_visible()
    case(mariam, premiere).check()
    expect(case(mariam, recu)).to_be_disabled()
    case(mariam, seconde).check()
    resume = mariam.get_by_role("region", name="Document pour la sélection")
    expect(resume).to_contain_text("Restaurant Le Baobab · 2 livraisons")
    expect(resume).to_contain_text(re.compile(r"4\s500 FCFA"))
    expect(mariam.get_by_label("Échéance de paiement")).not_to_have_value("")
    mariam.get_by_label("N° FNE (plateforme DGI)").fill("FNE-2026-000417")
    mariam.locator("input[name=fichier]").set_input_files(PDF_FNE)
    expect(resume).to_contain_text("fne-0417.pdf")
    mariam.get_by_role("button", name="Enregistrer le document").click()
    # La fiche du document s'ouvre, avec un seul message de confirmation.
    expect(mariam.get_by_role("status")).to_have_text(
        re.compile(r"^Document FAC-\d{4}-\d{6} enregistré pour Restaurant Le Baobab\.$")
    )
    expect(mariam.get_by_text("fne-0417.pdf")).to_be_visible()
    mariam.goto(reverse("ecran_facturation"))
    expect(mariam.get_by_role("link", name="À facturer · 1")).to_be_visible()

    fne = Facture.toutes_entreprises.get(numero_fne="FNE-2026-000417")
    assert (fne.montant_ttc, fne.choix_fait_par.first_name) == (4500, "Mariam")
    # La livraison reste enregistrée indépendamment de ce choix.
    for livraison in (premiere, seconde):
        assert Livraison.toutes_entreprises.get(pk=livraison.pk).statut == StatutLivraison.LIVREE

    # Autre document pour le Café : un reçu, avec le motif du traitement sans FNE.
    case(mariam, recu).check()
    mariam.get_by_label("Autre document (reçu, bon)").check()
    mariam.get_by_label("Document", exact=True).select_option(label="Reçu")
    mariam.get_by_label("Motif du traitement sans FNE").fill("Réglé comptant à la livraison")
    mariam.get_by_role("button", name="Enregistrer le document").click()
    expect(mariam.get_by_role("status")).to_contain_text("enregistré pour Café Le Plateau")
    mariam.goto(reverse("ecran_facturation"))
    expect(mariam.get_by_role("link", name="À facturer · 0")).to_be_visible()
    document_recu = Facture.toutes_entreprises.get(client__nom="Café Le Plateau")
    assert (document_recu.nature, document_recu.montant_ttc) == ("recu", 1000)

    # 2. §22-23 : la comptabilité (code de vérification) retrouve chaque document par
    # client, n° FNE, montant et date ; la liste se recharge pendant la saisie.
    serge = ouvrir(personne(Role.COMPTABILITE, first_name="Serge", last_name="A."))
    serge.goto(f"{reverse('ecran_facturation')}?vue=emis")
    liste = serge.locator("#liste-documents")
    recherche = serge.get_by_label("Rechercher")
    montant = serge.get_by_label("Montant (FCFA)")
    for texte, somme, attendu, absent in (
        ("baobab", "", fne, document_recu),
        ("FNE-2026-000417", "", fne, document_recu),
        (document_recu.numero_interne, "", document_recu, fne),
        ("", "1000", document_recu, fne),
    ):
        recherche.fill(texte)
        montant.fill(somme)
        montant.press("Tab")
        expect(liste).not_to_contain_text(absent.numero_interne)
        expect(liste).to_contain_text(attendu.numero_interne)
    montant.fill("")
    jour = fne.date_document.isoformat()
    serge.get_by_label("Du").fill(jour)
    serge.get_by_label("Au").fill(jour)
    serge.get_by_label("Au").press("Tab")
    expect(liste).to_contain_text(fne.numero_interne)
    expect(liste).to_contain_text(document_recu.numero_interne)

    # 3. §22-24 : 2 000 FCFA reçus du Baobab, répartis automatiquement sur la FNE ; la
    # facture reste partiellement payée.
    serge.goto(reverse("ecran_paiements"))
    serge.get_by_label("Client", exact=True).select_option(label="Restaurant Le Baobab")
    part = serge.get_by_label(f"Montant affecté {fne.numero_interne}")
    expect(part).to_be_visible()
    serge.get_by_label("Montant (FCFA)").fill("2000")
    expect(part).to_have_value("2000")
    expect(serge.get_by_text("Montant entièrement affecté")).to_contain_text(
        f"{fne.numero_interne} restera partiellement payée"
    )
    serge.get_by_label("Mode").select_option(label="Mobile Money")
    serge.get_by_label("Référence de transaction").fill("MM-1")
    serge.get_by_role("button", name="Enregistrer le paiement").click()
    expect(serge.get_by_text("Paiement de Restaurant Le Baobab enregistré.")).to_be_visible()

    # Le relevé du client s'ouvre aussitôt.
    expect(serge.get_by_role("heading", level=1)).to_have_text(
        "Relevé de compte · Restaurant Le Baobab"
    )
    expect(serge.get_by_text("Reste dû").locator("..")).to_contain_text(re.compile(r"2\s500"))
    expect(serge.get_by_text("Partiellement payé", exact=True)).to_be_visible()
    expect(serge.get_by_text("Payé", exact=True)).to_have_count(0)

    # Le solde, 3 000 FCFA en espèces : 2 500 soldent la facture, 500 restent en avance.
    serge.goto(reverse("ecran_paiements"))
    serge.get_by_label("Client", exact=True).select_option(label="Restaurant Le Baobab")
    expect(part).to_be_visible()
    serge.get_by_label("Montant (FCFA)").fill("3000")
    expect(part).to_have_value("2500")
    expect(serge.get_by_text("resteront en avance du client")).to_contain_text(
        re.compile(r"^500 FCFA")
    )
    serge.get_by_label("Mode").select_option(label="Espèces")
    serge.get_by_role("button", name="Enregistrer le paiement").click()
    expect(serge.get_by_text("Paiement de Restaurant Le Baobab enregistré.")).to_be_visible()
    expect(serge.get_by_text("Reste dû").locator("..")).to_contain_text("0")
    expect(serge.get_by_text("Payé", exact=True)).to_be_visible()
    assert Paiement.toutes_entreprises.filter(client_id=fne.client_id).count() == 2
    # Le relevé s'exporte en PDF.
    pdf = serge.request.get(reverse("releve_client_pdf", args=[fne.client_id]))
    assert (pdf.status, pdf.headers["content-type"]) == (200, "application/pdf")
