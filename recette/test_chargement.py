"""Recette §22-17, 18 et 25 dans le navigateur : la gestionnaire compose la tournée, la
préparatrice compte sur son téléphone, l'écart est décidé par la Direction, le chauffeur
confirme lui-même la réception puis démarre ; un autre chauffeur ne voit rien."""

import datetime

import pytest
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from catalogue.tests.factories import ProduitFactory
from clients.tests.factories import AdresseFactory
from commandes import services as commandes
from commandes.models import Canal, Commande, Statut
from comptes.models import Role
from logistique.models import Chargement, Livraison, StatutChargement, StatutTournee, Tournee
from noyau.contexte import activer_entreprise, activer_utilisateur
from production import services as production
from production.models import PlanProduction

pytestmark = pytest.mark.recette


def demain():
    return timezone.localdate() + datetime.timedelta(days=1)


@pytest.fixture
def acteurs(personne):
    return {
        "aminata": personne(Role.GESTIONNAIRE, first_name="Aminata", last_name="K."),
        "ibrahim": personne(Role.DIRECTION, first_name="Ibrahim", last_name="T."),
        "fatou": personne(Role.COMMERCIAL, first_name="Fatou", last_name="C."),
        "awa": personne(Role.PREPARATION, first_name="Awa", last_name="D."),
        "konan": personne(Role.CHAUFFEUR, first_name="Konan", last_name="K."),
        "yao": personne(Role.CHAUFFEUR, first_name="Yao", last_name="S."),
    }


@pytest.fixture
def pretes(entreprise, acteurs):
    """Deux commandes du lendemain, produites et prêtes au chargement (Sprint 3)."""
    with activer_entreprise(entreprise):
        baguette = ProduitFactory(entreprise=entreprise, nom="Pain baguette")
        croissant = ProduitFactory(entreprise=entreprise, nom="Croissant")
        resultat = []
        for nom, commune in (
            ("Restaurant Le Baobab", "Cocody"),
            ("Supérette Les Palmiers", "Marcory"),
        ):
            adresse = AdresseFactory(
                client__entreprise=entreprise, client__nom=nom, commune=commune, par_defaut=True
            )
            with activer_utilisateur(acteurs["fatou"]):
                commande = commandes.creer_commande(
                    client=adresse.client,
                    lignes=[(baguette, 100), (croissant, 60)],
                    canal=Canal.WHATSAPP,
                    date_livraison=demain(),
                    creneau="06h–08h",
                )
                commandes.soumettre_commande(commande)
            with activer_utilisateur(acteurs["aminata"]):
                commandes.confirmer_commande(commande)
            resultat.append(commande)
        with activer_utilisateur(acteurs["aminata"]):
            plan = PlanProduction.objects.get(date_livraison=demain())
            production.valider_plan(plan)
            for besoin in plan.besoins.all():
                production.enregistrer_saisie(besoin, besoin.quantite_a_produire)
            production.marquer_plan_pret(plan)
        assert {c.statut for c in Commande.objects.all()} == {Statut.PRETE_CHARGEMENT}
    return resultat


def test_22_17_18_25_du_chargement_au_depart(ouvrir, entreprise, acteurs, pretes):
    baobab, palmiers = pretes
    tournees = f"{reverse('ecran_tournees')}?date={demain().isoformat()}"

    # 1. La gestionnaire crée la tournée de Konan et y affecte les deux commandes.
    bureau = ouvrir(acteurs["aminata"])
    bureau.goto(tournees)
    bureau.get_by_text("+ Nouvelle tournée").click()
    bureau.get_by_label("Chauffeur ou prestataire").select_option(label="Konan K.")
    bureau.get_by_role("button", name="Créer la tournée du").click()
    for restantes in ("1", "0"):
        bureau.get_by_role("button", name="Affecter à…").first.click()
        expect(bureau.get_by_role("heading", name="À affecter")).to_have_text(
            f"À affecter {restantes}"
        )
    tournee = Tournee.toutes_entreprises.get(entreprise=entreprise)
    carte = bureau.get_by_role("region", name=tournee.numero)
    expect(carte).to_contain_text("Konan K.")
    expect(carte.get_by_text("vérifiées")).to_contain_text("0/2")

    # 2. §22-17 : la préparatrice compte sur son téléphone. Le Baobab est conforme :
    # cocher une ligne reprend la quantité attendue.
    preparation = ouvrir(acteurs["awa"], telephone=True)
    preparation.goto(f"{reverse('ecran_chargement')}?date={demain().isoformat()}")
    preparation.get_by_role("link", name="1. Restaurant Le Baobab").click()
    for comptees, produit in enumerate(("Pain baguette", "Croissant"), start=1):
        preparation.get_by_label(produit).check()
        expect(preparation.get_by_text("Conforme")).to_have_count(comptees)
    preparation.get_by_role("button", name="Valider le chargement").click()

    # L'écran passe à l'arrêt suivant. Les Palmiers : 30 croissants au lieu de 60,
    # motif choisi, écart signalé.
    expect(preparation.get_by_role("heading", level=1)).to_have_text(
        "Arrêt 2 · Supérette Les Palmiers"
    )
    preparation.get_by_label("Pain baguette").check()
    expect(preparation.get_by_text("Conforme")).to_have_count(1)
    croissants = preparation.locator("form", has_text="Croissant").get_by_label("Préparé")
    croissants.fill("30")
    croissants.press("Tab")
    expect(preparation.get_by_text("Écart -30")).to_be_visible()
    preparation.get_by_label("Motif de l'écart").select_option(value="production_insuffisante")
    preparation.get_by_label("Commentaire pour le gestionnaire").fill("30 croissants manquants")
    preparation.get_by_role("button", name="Signaler l'écart au gestionnaire").click()
    expect(preparation.get_by_text("Écart constaté")).to_be_visible()

    # §22-18 : le chauffeur voit l'écart ; le départ est bloqué.
    konan = ouvrir(acteurs["konan"], telephone=True)
    konan.goto(reverse("ecran_ma_tournee"))
    expect(konan.get_by_role("heading", name="Restaurant Le Baobab")).to_be_visible()
    expect(konan.get_by_text("Écart : -30 Croissant")).to_be_visible()
    depart = konan.get_by_role("button", name="Démarrer la tournée")
    expect(depart).to_be_disabled()
    expect(depart).to_contain_text("2 commandes restantes")

    # §22-25 : Yao ne voit pas la tournée de Konan, même par son adresse.
    yao = ouvrir(acteurs["yao"], telephone=True)
    yao.goto(reverse("ecran_ma_tournee"))
    expect(yao.get_by_text("Aucune tournée ne vous est affectée")).to_be_visible()
    expect(yao.get_by_text(tournee.numero)).to_have_count(0)
    assert yao.goto(reverse("reception_tournee", args=[tournee.pk])).status == 403

    # 3. La Direction (code de vérification) accepte l'écart depuis l'écran Tournées.
    direction = ouvrir(acteurs["ibrahim"])
    direction.goto(tournees)
    direction.get_by_role("button", name="Accepter").click()
    expect(direction.get_by_role("button", name="Accepter")).to_have_count(0)

    # 4. Konan confirme lui-même chaque commande (30 croissants retenus), puis démarre.
    konan.reload()
    for client in ("Restaurant Le Baobab", "Supérette Les Palmiers"):
        arret = konan.locator("section", has=konan.get_by_role("heading", name=client))
        if client == "Supérette Les Palmiers":
            expect(arret.get_by_label("Croissant")).to_have_value("30")
        arret.get_by_role("button", name="Je confirme avoir reçu cette commande").click()
        expect(konan.get_by_text("Reçue par Konan K.").first).to_be_visible()
    depart = konan.get_by_role("button", name="Démarrer la tournée")
    expect(depart).to_be_enabled()
    depart.click()

    # 5. Cascade : tournée partie, commandes en livraison, une livraison par chargement.
    tournee.refresh_from_db()
    assert tournee.statut == StatutTournee.EN_COURS and tournee.depart_le
    for commande in pretes:
        commande.refresh_from_db()
        assert commande.statut == Statut.EN_LIVRAISON
    assert Livraison.toutes_entreprises.filter(chargement__tournee=tournee).count() == 2

    # §22-18 : heure, deux identités et écart conservés, visibles sur la fiche commande.
    chargement = Chargement.toutes_entreprises.get(tournee=tournee, commande=palmiers)
    assert chargement.statut == StatutChargement.RECUE_CHAUFFEUR
    assert (chargement.verifie_par, chargement.recu_par) == (acteurs["awa"], acteurs["konan"])
    assert chargement.verifie_le and chargement.recu_le
    bureau.goto(reverse("commande_detail", args=[palmiers.pk]))
    historique = bureau.locator("section", has=bureau.get_by_role("heading", name="Historique"))
    for attendu in (
        f"Affectée à la tournée {tournee.numero}",
        "Écart constaté au chargement",
        "Exception pour l'écart au chargement acceptée",
        "Réception confirmée par le livreur",
        "Départ de la tournée : en livraison",
        "Awa D.",
        "Konan K.",
    ):
        expect(historique).to_contain_text(attendu)
