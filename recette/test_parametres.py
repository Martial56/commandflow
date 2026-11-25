"""Recette de l'écran Paramètres (maquette 17, décision 8.6) : un gestionnaire crée le compte
d'un chauffeur, qui se connecte sur son téléphone avec le mot de passe provisoire et doit le
remplacer ; puis le gestionnaire règle les preuves exigées."""

import re

import pytest
from django.urls import reverse
from playwright.sync_api import expect

from comptes.models import Role, Utilisateur
from entreprises.models import Entreprise
from entreprises.parametres import preuves_obligatoires

pytestmark = pytest.mark.recette


def test_compte_cree_puis_mot_de_passe_remplace(
    ouvrir, personne, entreprise, browser, playwright, live_server
):
    bureau = ouvrir(personne(Role.GESTIONNAIRE, first_name="Aminata", last_name="K."))
    bureau.goto(reverse("ecran_parametres"))
    expect(bureau.get_by_role("heading", name="Utilisateurs et rôles")).to_be_visible()

    # 1. Création du compte : le mot de passe provisoire s'affiche une seule fois.
    bureau.get_by_text("+ Ajouter un utilisateur").click()
    bureau.locator("#id_prenom").fill("Yao")
    bureau.locator("#id_nom").fill("S.")
    bureau.locator("#id_telephone").fill("+225 07 11 22 33 44")
    bureau.locator("#id_role").select_option(label="Chauffeur")
    bureau.get_by_role("button", name="Créer le compte").click()
    expect(bureau.get_by_text("Compte de Yao S. créé.")).to_be_visible()
    provisoire = bureau.locator("[data-mot-de-passe]").inner_text()
    assert re.fullmatch(r"\w{4}-\w{4}-\w{4}", provisoire)
    ligne = bureau.locator("tr", has_text="Yao S.")
    expect(ligne).to_contain_text("mot de passe provisoire")

    # 2. Le chauffeur se connecte sur son téléphone et doit d'abord changer son mot de passe.
    contexte = browser.new_context(**playwright.devices["Pixel 7"], base_url=live_server.url)
    telephone = contexte.new_page()
    telephone.goto(reverse("account_login"))
    telephone.get_by_label("Téléphone ou e-mail").fill("+2250711223344")
    telephone.get_by_label("Mot de passe").fill(provisoire)
    telephone.get_by_role("button", name="Se connecter").click()
    expect(telephone).to_have_url(re.compile(re.escape(reverse("account_change_password")) + "$"))
    telephone.goto(reverse("ecran_ma_tournee"))  # toute autre page ramène au changement
    expect(telephone).to_have_url(re.compile(re.escape(reverse("account_change_password")) + "$"))
    champs = telephone.locator("input[type=password]")
    champs.nth(0).fill(provisoire)
    champs.nth(1).fill("Baguette-2026!")
    champs.nth(2).fill("Baguette-2026!")
    champs.nth(2).press("Enter")
    expect(telephone).to_have_url(re.compile(re.escape(reverse("ecran_ma_tournee")) + "$"))
    compte = Utilisateur.objects.get(telephone="+2250711223344")
    assert not compte.doit_changer_mot_de_passe
    contexte.close()

    # 3. Le gestionnaire exige photo et signature pour les restaurants.
    bureau.goto(reverse("ecran_parametres"))
    bureau.get_by_label("Restaurant : réceptionnaire").uncheck()
    bureau.get_by_label("Restaurant : signature").check()
    bureau.get_by_label("Règle pour restaurant").select_option(label="Tous")
    bureau.get_by_role("button", name="Enregistrer les preuves").click()
    expect(bureau.get_by_text("Preuves de livraison enregistrées.")).to_be_visible()
    reglees = Entreprise.objects.get(pk=entreprise.pk)
    assert preuves_obligatoires(reglees, "restaurant") == [["photo_bordereau"], ["signature"]]


def test_produit_cree_puis_propose_dans_une_commande(ouvrir, personne, entreprise):
    """Décision 8.6 : le gestionnaire crée un produit dans Paramètres ; il est aussitôt
    proposé dans une nouvelle commande, à son prix ; désactivé, il ne l'est plus."""
    bureau = ouvrir(personne(Role.GESTIONNAIRE))
    bureau.goto(reverse("ecran_parametres"))
    bureau.get_by_text("+ Ajouter un produit").click()
    bureau.locator("#id_nouveau-produit-code").fill("PCHOC")
    bureau.locator("#id_nouveau-produit-nom").fill("Pain au chocolat")
    bureau.locator("#id_nouveau-produit-unite").select_option(label="pièce")
    bureau.locator("#id_nouveau-produit-prix_reference").fill("350")
    bureau.get_by_role("button", name="Créer le produit").click()
    expect(bureau.get_by_text("Produit Pain au chocolat (PCHOC) créé.")).to_be_visible()
    ligne = bureau.locator("#s-produits tr", has_text="PCHOC")
    expect(ligne).to_contain_text("Actif")

    # Proposé dans une nouvelle commande, avec son prix.
    bureau.goto(reverse("commande_nouvelle"))
    choix = bureau.locator("select[name^=produit_]").first
    choix.select_option(label="Pain au chocolat")
    bureau.locator("input[name^=quantite_]").first.fill("10")
    bureau.locator("input[name^=quantite_]").first.press("Tab")
    expect(bureau.locator("#total")).to_have_text(re.compile(r"^3\D500$"))

    # Prix modifié puis produit désactivé : il disparaît des nouvelles commandes.
    bureau.goto(reverse("ecran_parametres"))
    ligne = bureau.locator("#s-produits tr", has_text="PCHOC")
    ligne.get_by_text("Modifier").click()
    ligne.get_by_label("Prix TTC (FCFA)").fill("375")
    ligne.get_by_role("button", name="Enregistrer Pain au chocolat").click()
    expect(bureau.get_by_text("Les commandes déjà saisies gardent leur prix.")).to_be_visible()
    # Historique des prix (lot B9) : le plus récent d'abord.
    ligne = bureau.locator("#s-produits tr", has_text="PCHOC")
    ligne.get_by_text("Historique des prix").click()
    prix = ligne.locator("[data-historique-prix] li")
    expect(prix).to_have_count(2)
    expect(prix.nth(0)).to_contain_text("375")
    expect(prix.nth(1)).to_contain_text("350")
    expect(prix.nth(1)).to_contain_text("création")
    bureau.once("dialog", lambda dialogue: dialogue.accept())
    bureau.locator("#s-produits tr", has_text="PCHOC").get_by_role(
        "button", name="Désactiver"
    ).click()
    expect(bureau.locator("#s-produits tr", has_text="PCHOC")).to_contain_text("Désactivé")
    bureau.goto(reverse("commande_nouvelle"))
    expect(
        bureau.locator("select[name^=produit_] option", has_text="Pain au chocolat")
    ).to_have_count(0)
