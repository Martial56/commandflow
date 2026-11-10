"""Recette §22-13 à 16 dans le navigateur : saisie d'une commande, confirmation des
récurrentes, plan de production et fournées saisies sur téléphone."""

import datetime
import re

import pytest
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from catalogue.tests.factories import ProduitFactory
from clients.tests.factories import AdresseFactory
from commandes import services as commandes
from commandes.models import (
    Canal,
    Commande,
    LigneModeleRecurrent,
    ModeleRecurrent,
    Statut,
)
from commandes.tasks import generer_recurrentes
from comptes.models import Role
from noyau.contexte import activer_entreprise, activer_utilisateur
from production.models import BesoinProduit, PlanProduction, SaisieProduction

pytestmark = pytest.mark.recette


def demain():
    return timezone.localdate() + datetime.timedelta(days=1)


def lisible(locator):
    """Texte d'un bloc sur une seule ligne (espaces insécables et retours compris)."""
    return " ".join(locator.inner_text().replace(" ", " ").replace("\xa0", " ").split())


@pytest.fixture
def produits(entreprise):
    with activer_entreprise(entreprise):
        return {
            "baguette": ProduitFactory(
                entreprise=entreprise, nom="Pain baguette", prix_reference=150
            ),
            "croissant": ProduitFactory(entreprise=entreprise, nom="Croissant", prix_reference=300),
        }


def client_avec_adresse(entreprise, nom, code):
    with activer_entreprise(entreprise):
        return AdresseFactory(
            client__entreprise=entreprise, client__nom=nom, client__code=code, par_defaut=True
        )


def test_22_13_commande_saisie_a_l_ecran(ouvrir, personne, entreprise, produits):
    """Numéro unique attribué à l'enregistrement ; l'identité du créateur est conservée."""
    commercial = personne(Role.COMMERCIAL, first_name="Koffi", last_name="B.")
    gestionnaire = personne(Role.GESTIONNAIRE, first_name="Aminata", last_name="K.")
    client_avec_adresse(entreprise, "Restaurant Le Baobab", "CLI-000001")

    page = ouvrir(commercial)
    page.goto(reverse("commande_nouvelle"))
    # Le client choisi dans la liste charge ses adresses (htmx).
    page.locator("#id_client").fill("Restaurant Le Baobab — CLI-000001")
    page.locator("#id_client").press("Tab")
    expect(page.get_by_text("Choisir d'abord le client.")).to_have_count(0)
    page.locator("#id_date_livraison").fill(demain().isoformat())
    page.locator("#id_creneau").select_option("06h–08h")

    # Deux lignes : montants et total recalculés par le serveur à chaque changement.
    page.locator("select[name^=produit_]").nth(0).select_option(label="Pain baguette")
    page.locator("input[name^=quantite_]").nth(0).fill("120")
    page.locator("input[name^=quantite_]").nth(0).press("Tab")
    expect(page.locator("#total")).to_have_text(re.compile(r"^18\D000$"))
    page.get_by_role("button", name="+ Ajouter un produit").click()
    expect(page.locator("select[name^=produit_]")).to_have_count(2)
    page.locator("select[name^=produit_]").nth(1).select_option(label="Croissant")
    page.locator("input[name^=quantite_]").nth(1).fill("40")
    page.locator("input[name^=quantite_]").nth(1).press("Tab")
    expect(page.locator("#total")).to_have_text(re.compile(r"^30\D000$"))

    page.get_by_role("button", name="Soumettre pour confirmation").click()
    titre = page.get_by_role("heading", level=1)
    expect(titre).to_have_text(re.compile(rf"^CMD-{timezone.localdate().year}-\d{{6}}$"))
    numero = titre.inner_text()
    commande = Commande.toutes_entreprises.get(numero=numero)
    assert (commande.statut, commande.cree_par) == (Statut.A_CONFIRMER, commercial)
    with activer_entreprise(entreprise):  # prix figés sur les lignes
        assert sorted(commande.lignes.values_list("prix_unitaire", flat=True)) == [150, 300]

    # Le gestionnaire confirme : le créateur reste le commercial, l'historique le montre.
    bureau = ouvrir(gestionnaire)
    bureau.goto(reverse("commande_detail", args=[commande.pk]))
    bureau.get_by_label("Note de confirmation (facultative)").fill("Confirmé par téléphone")
    bureau.get_by_role("button", name="Confirmer avec le client").click()
    expect(bureau.get_by_text("À planifier").first).to_be_visible()
    historique = bureau.locator("section", has=bureau.get_by_role("heading", name="Historique"))
    expect(historique).to_contain_text("Commande créée")
    expect(historique).to_contain_text("Koffi B.")
    commande.refresh_from_db()
    assert (commande.statut, commande.cree_par) == (Statut.A_PLANIFIER, commercial)
    assert Commande.toutes_entreprises.filter(numero=numero).count() == 1


def test_22_14_recurrentes_en_attente_jusqu_a_confirmation(ouvrir, personne, entreprise, produits):
    """Les récurrentes générées attendent la confirmation quotidienne, faite à l'écran."""
    with activer_entreprise(entreprise):
        for nom, code, quantite in [
            ("Hôtel Ivoire Lagune", "CLI-000010", 200),
            ("Café Le Plateau", "CLI-000011", 60),
        ]:
            adresse = client_avec_adresse(entreprise, nom, code)
            modele = ModeleRecurrent.objects.create(
                client=adresse.client,
                adresse=adresse,
                jours_semaine=list(range(7)),
                creneau="06h–08h",
                date_debut=timezone.localdate(),
            )
            LigneModeleRecurrent.objects.create(
                modele=modele, produit=produits["baguette"], quantite=quantite
            )

    veille = timezone.localdate()
    generer_recurrentes(maintenant=f"{veille}T18:00:00")
    generer_recurrentes(maintenant=f"{veille}T18:15:00")  # relance : aucun doublon
    generees = Commande.toutes_entreprises.filter(entreprise=entreprise)
    assert generees.count() == 2
    assert set(generees.values_list("statut", flat=True)) == {Statut.A_CONFIRMER}

    page = ouvrir(personne(Role.COMMERCIAL))
    page.goto(f"{reverse('ecran_recurrentes')}?date={demain().isoformat()}")
    expect(page.get_by_text("0 / 2 confirmées")).to_be_visible()
    # Rien n'avance sans action : la page affichée ne confirme rien.
    assert set(generees.values_list("statut", flat=True)) == {Statut.A_CONFIRMER}

    # Sélection de toutes les lignes (Alpine), puis confirmation groupée.
    page.get_by_label("Tout sélectionner").check()
    expect(page.get_by_text("2 commandes sélectionnées")).to_be_visible()
    page.get_by_role("button", name="Confirmer la sélection").click()
    expect(page.get_by_text("2 / 2 confirmées")).to_be_visible()
    assert set(generees.values_list("statut", flat=True)) == {Statut.A_PLANIFIER}


def test_22_15_16_plan_regroupe_et_fournees_cumulees(ouvrir, personne, entreprise, produits):
    """Regroupement par produit et date, fournées cumulées depuis deux téléphones, écarts
    entre nécessaire, produit et disponible visibles par le responsable."""
    commercial = personne(Role.COMMERCIAL)
    baguette, croissant = produits["baguette"], produits["croissant"]
    with activer_entreprise(entreprise), activer_utilisateur(commercial):
        for nom, code, lignes in [
            ("Restaurant Le Baobab", "CLI-000001", [(baguette, 120), (croissant, 40)]),
            ("Hôtel Ivoire Lagune", "CLI-000002", [(baguette, 200)]),
            ("Café Le Plateau", "CLI-000003", [(baguette, 60), (croissant, 30)]),
        ]:
            adresse = client_avec_adresse(entreprise, nom, code)
            commande = commandes.creer_commande(
                client=adresse.client,
                lignes=lignes,
                canal=Canal.TELEPHONE,
                date_livraison=demain(),
                creneau="06h–08h",
            )
            commandes.soumettre_commande(commande)
            commandes.confirmer_commande(commande)

    # §22-15 : un plan pour la date, les quantités regroupées par produit.
    responsable = ouvrir(personne(Role.PRODUCTION, first_name="Fatou", last_name="D."))
    responsable.goto(f"{reverse('ecran_production')}?date={demain().isoformat()}")
    expect(responsable.get_by_text("Regroupe 3 commandes confirmées")).to_be_visible()
    ligne_baguette = responsable.locator("tr", has_text="Pain baguette")
    expect(ligne_baguette).to_contain_text("380")
    expect(responsable.locator("tr", has_text="Croissant")).to_contain_text("70")

    # Ajustement de « à produire » (htmx, ligne rechargée seule), puis validation.
    champ = responsable.get_by_label("À produire pain baguette")
    champ.fill("400")
    champ.press("Tab")
    expect(responsable.locator("tr", has_text="Pain baguette")).to_contain_text("+20")
    responsable.get_by_role("button", name="Valider le plan").click()
    expect(responsable.get_by_text("Validé", exact=True)).to_be_visible()

    # Deux équipes saisissent leurs fournées de baguettes, chacune sur son téléphone.
    plan = PlanProduction.toutes_entreprises.get(entreprise=entreprise)
    besoin = BesoinProduit.toutes_entreprises.get(plan=plan, produit=baguette)
    saisie = f"{reverse('production_saisie')}?date={demain().isoformat()}&produit={baguette.pk}"
    for prenom, quantite, plus_dix in [("Awa", "200", False), ("Moussa", "120", True)]:
        telephone = ouvrir(
            personne(Role.PRODUCTION, first_name=prenom, last_name="T."), telephone=True
        )
        telephone.goto(saisie)
        expect(telephone.get_by_role("heading", name="Pain baguette", exact=True)).to_be_visible()
        telephone.get_by_label("Nouvelle fournée").fill(quantite)
        if plus_dix:  # bouton « + » (Alpine) : 120 + 10
            telephone.get_by_role("button", name="Ajouter 10").click()
            expect(telephone.get_by_label("Nouvelle fournée")).to_have_value("130")
        telephone.get_by_label("Lot").fill(f"Lot {prenom}")
        telephone.get_by_role("button", name=re.compile(r"^Enregistrer")).click()
        expect(
            telephone.get_by_role("heading", name="Dernières saisies · Pain baguette")
        ).to_be_visible()
    saisies = telephone.locator("section", has_text="Dernières saisies")
    expect(saisies).to_contain_text("Awa T.")
    expect(saisies).to_contain_text("Moussa T.")

    # Les saisies s'additionnent sans s'écraser.
    assert SaisieProduction.toutes_entreprises.filter(besoin=besoin).count() == 2
    besoin.refresh_from_db()
    assert besoin.quantite_produite == 330

    # §22-16 : le responsable voit nécessaire, produit, manque et disponible.
    responsable.goto(
        f"{reverse('ecran_production')}?date={demain().isoformat()}&produit={baguette.pk}"
    )
    assert "Commandé 380 À produire 400 Produit 330 Manque 70 Réservé 0 Disponible 330" in lisible(
        responsable.locator("#detail")
    )
    alerte = responsable.get_by_role("status").filter(has_text="Production insuffisante")
    expect(alerte).to_contain_text("Pain baguette (manque 70)")
    expect(alerte).to_contain_text("Croissant (manque 70)")
    expect(responsable.locator("tr", has_text="Pain baguette")).to_contain_text("Manque 70")
    expect(responsable.locator("tr", has_text="Croissant")).to_contain_text("À lancer")
