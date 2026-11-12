"""Recette §22-26 dans le navigateur : les comptes d'une autre entreprise parcourent tous
les écrans et tentent les adresses directes ; aucune donnée de l'entreprise pilote
n'apparaît, aucune fiche ne s'ouvre."""

# Fixtures reprises des recettes précédentes (pytest les injecte par leur nom).
# ruff: noqa: F811

import io

import pytest
from django.urls import reverse
from openpyxl import load_workbook
from playwright.sync_api import expect

from clients.tests.factories import AdresseFactory
from comptes.models import Role
from entreprises.tests.factories import EntrepriseFactory
from facturation import services as facturation
from facturation.models import TypeDocument
from facturation.tests.test_services import livree
from logistique.models import Chargement, Livraison
from noyau.contexte import activer_entreprise, activer_utilisateur
from paiements import services as paiements
from production.models import PlanProduction
from recette.test_chargement import acteurs, demain, pretes  # noqa: F401
from recette.test_livraison import en_route  # noqa: F401

pytestmark = pytest.mark.recette


@pytest.fixture
def entreprise_pilote(entreprise, personne, acteurs, pretes, en_route):
    """L'entreprise A au complet : commandes, plan validé, tournée partie, document FNE
    et paiement. Renvoie ce qui ne doit jamais apparaître chez B."""
    with activer_entreprise(entreprise):
        plateau = AdresseFactory(client__entreprise=entreprise, client__nom="Café Le Plateau")
        livraison = livree(plateau, livre=4)
        with activer_utilisateur(personne(Role.FACTURATION)):
            facture = facturation.enregistrer_choix(
                [livraison], type_document=TypeDocument.FNE, numero_fne="FNE-2026-000999"
            )
        with activer_utilisateur(acteurs["aminata"]):
            paiements.enregistrer_paiement(
                client=plateau.client, montant=600, mode="especes", affectations={facture: 600}
            )
    baobab, palmiers = pretes
    return {
        "textes": [
            "Restaurant Le Baobab",
            "Supérette Les Palmiers",
            "Café Le Plateau",
            "Pain baguette",
            baobab.numero,
            palmiers.numero,
            en_route.numero,
            facture.numero_interne,
            "FNE-2026-000999",
        ],
        "commande": baobab,
        "client": baobab.client,
        "facture": facture,
        "chargement": Chargement.toutes_entreprises.get(tournee=en_route, commande=baobab),
        "livraison": Livraison.toutes_entreprises.get(commande=baobab),
        "tournee": en_route,
        "plan": PlanProduction.toutes_entreprises.get(entreprise=entreprise),
    }


def sans_donnee_de_a(page, textes, adresse):
    contenu = page.locator("body").inner_text()
    fuites = [texte for texte in textes if texte in contenu]
    assert not fuites, (adresse, fuites)


def test_22_26_rien_de_l_entreprise_a_depuis_l_entreprise_b(ouvrir, personne, entreprise_pilote):
    a = entreprise_pilote
    textes = a["textes"]
    autre = EntrepriseFactory(nom="Boulangerie du Nord")
    jour = demain().isoformat()

    # Contre-épreuve : dans l'entreprise A, ces données sont bien visibles.
    gestionnaire_a = ouvrir(personne(Role.GESTIONNAIRE))
    gestionnaire_a.goto(f"{reverse('ecran_tournees')}?date={jour}")
    expect(gestionnaire_a.locator("main")).to_contain_text(a["tournee"].numero)
    gestionnaire_a.goto(f"{reverse('ecran_facturation')}?vue=emis")
    expect(gestionnaire_a.locator("main")).to_contain_text("FNE-2026-000999")
    gestionnaire_a.goto(reverse("ecran_commandes"))
    expect(gestionnaire_a.locator("main")).to_contain_text("Restaurant Le Baobab")

    # La Direction de B (code de vérification) parcourt tous les écrans du bureau.
    direction = ouvrir(personne(Role.DIRECTION, dans=autre))
    for adresse in (
        reverse("ecran_tableau_de_bord"),
        reverse("ecran_commandes"),
        f"{reverse('ecran_recurrentes')}?date={jour}",
        reverse("ecran_clients"),
        f"{reverse('ecran_production')}?date={jour}",
        f"{reverse('ecran_tournees')}?date={jour}",
        reverse("ecran_facturation"),
        f"{reverse('ecran_facturation')}?vue=emis",
        f"{reverse('ecran_facturation')}?vue=emis&q=FNE-2026-000999",
        reverse("ecran_paiements"),
        reverse("notifications"),
    ):
        reponse = direction.goto(adresse)
        assert reponse.status == 200, adresse
        sans_donnee_de_a(direction, textes, adresse)
    expect(direction.get_by_role("navigation").first).to_contain_text("Boulangerie du Nord")

    # Les fiches de A, ouvertes par leur adresse, n'existent pas pour B.
    for adresse in (
        reverse("commande_detail", args=[a["commande"].pk]),
        reverse("client_detail", args=[a["client"].pk]),
        reverse("facture_detail", args=[a["facture"].pk]),
        reverse("releve_client", args=[a["client"].pk]),
        reverse("releve_client_pdf", args=[a["client"].pk]),
        reverse("production_fiche_atelier", args=[a["plan"].pk]),
    ):
        reponse = direction.goto(adresse)
        assert reponse.status == 404, adresse
        sans_donnee_de_a(direction, textes, adresse)

    # Les exports de B ne contiennent que l'entête.
    classeur = load_workbook(io.BytesIO(direction.request.get(reverse("creances_excel")).body()))
    assert len(list(classeur["Factures ouvertes"].values)) == 1

    # Sur le terrain : chauffeur, préparatrice et atelier de B.
    chauffeur = ouvrir(personne(Role.CHAUFFEUR, dans=autre), telephone=True)
    chauffeur.goto(reverse("ecran_ma_tournee"))
    expect(chauffeur.get_by_text("Aucune tournée ne vous est affectée")).to_be_visible()
    for adresse in (
        reverse("reception_tournee", args=[a["tournee"].pk]),
        reverse("livraison_saisie", args=[a["livraison"].pk]),
    ):
        assert chauffeur.goto(adresse).status in (403, 404), adresse
        sans_donnee_de_a(chauffeur, textes, adresse)

    preparation = ouvrir(personne(Role.PREPARATION, dans=autre), telephone=True)
    preparation.goto(f"{reverse('ecran_chargement')}?date={jour}")
    sans_donnee_de_a(preparation, textes, "ecran_chargement")
    adresse = reverse("chargement_controle", args=[a["chargement"].pk])
    assert preparation.goto(adresse).status in (403, 404)
    sans_donnee_de_a(preparation, textes, adresse)

    atelier = ouvrir(personne(Role.PRODUCTION, dans=autre), telephone=True)
    atelier.goto(f"{reverse('production_saisie')}?date={jour}")
    expect(atelier.get_by_text("Aucun plan de production validé")).to_be_visible()
    sans_donnee_de_a(atelier, textes, "production_saisie")
