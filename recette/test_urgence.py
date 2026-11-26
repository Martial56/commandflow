"""Recette du lot B3 sur téléphone : Konan est parti livrer ; une urgence lui est confiée
dans une nouvelle tournée. Prévenu par la cloche, il la reçoit au dépôt, la démarre et la
livre, puis reprend sa première tournée par le bandeau « Autres tournées aujourd'hui »."""

# Fixtures reprises de la recette du chargement (pytest les injecte par leur nom).
# ruff: noqa: F811

import pytest
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from logistique import services as logistique
from logistique.models import LigneChargement, Livraison, StatutLivraison, StatutTournee
from logistique.tests.test_services_tournee import commande_prete
from noyau.contexte import activer_entreprise, activer_utilisateur
from recette.test_chargement import acteurs  # noqa: F401

pytestmark = pytest.mark.recette


def charger(tournee, acteurs):
    """Une commande du jour affectée à la tournée et vérifiée par Awa (préparation)."""
    with activer_utilisateur(acteurs["aminata"]):
        chargement = logistique.affecter_commande(
            tournee, commande_prete(tournee.entreprise, date=tournee.date)
        )
    with activer_utilisateur(acteurs["awa"]):
        for ligne in LigneChargement.objects.filter(chargement=chargement):
            logistique.compter_ligne(ligne, ligne.quantite_attendue)
        logistique.valider_preparation(chargement)
    return chargement


@pytest.fixture
def urgence_apres_le_depart(entreprise, acteurs):
    """Première tournée de Konan partie ; l'urgence est chargée dans une seconde tournée."""
    with activer_entreprise(entreprise):
        with activer_utilisateur(acteurs["aminata"]):
            premiere = logistique.creer_tournee(
                date=timezone.localdate(), chauffeur=acteurs["konan"]
            )
        chargement = charger(premiere, acteurs)
        with activer_utilisateur(acteurs["konan"]):
            lignes = LigneChargement.objects.filter(chargement=chargement)
            logistique.confirmer_reception(
                chargement, {ligne.pk: ligne.quantite_attendue for ligne in lignes}
            )
            premiere.refresh_from_db()
            logistique.demarrer_tournee(premiere)
        with activer_utilisateur(acteurs["aminata"]):
            seconde = logistique.creer_tournee(
                date=timezone.localdate(), chauffeur=acteurs["konan"]
            )
        charger(seconde, acteurs)
    return premiere, seconde


def test_urgence_dans_une_seconde_tournee_du_meme_chauffeur(
    ouvrir, entreprise, acteurs, urgence_apres_le_depart
):
    premiere, seconde = urgence_apres_le_depart
    konan = ouvrir(acteurs["konan"], telephone=True)
    konan.goto(reverse("ecran_ma_tournee"))

    # 1. Sa tournée partie reste affichée ; la cloche annonce la nouvelle tournée.
    expect(konan.get_by_role("heading", name="Ma tournée", level=1)).to_be_visible()
    expect(konan.locator("[data-non-lues]")).to_be_visible()
    konan.get_by_role("link", name="Notifications").click()
    expect(konan.get_by_text(f"Nouvelle tournée {seconde.numero} aujourd'hui")).to_be_visible()
    konan.get_by_role("link", name="Retour à mon écran").click()

    # 2. Bandeau : la tournée de l'urgence, à recevoir au dépôt.
    bandeau = konan.get_by_role("navigation", name="Autres tournées aujourd'hui")
    expect(bandeau).to_contain_text(f"{seconde.numero} Chargement à recevoir")
    bandeau.get_by_role("link").click()
    expect(konan.get_by_role("heading", name="Contrôle du chargement", level=1)).to_be_visible()
    expect(konan.get_by_role("navigation", name="Autres tournées aujourd'hui")).to_contain_text(
        f"{premiere.numero} En cours · 1 arrêt restant"
    )

    # 3. Il confirme la réception et démarre : les deux tournées sont en cours (Q1 a).
    konan.get_by_role("button", name="Je confirme avoir reçu cette commande").click()
    expect(konan.get_by_text("Reçue par Konan K.")).to_be_visible()
    depart = konan.get_by_role("button", name="Démarrer la tournée")
    expect(depart).to_be_enabled()
    depart.click()
    expect(konan.get_by_text("Arrêt 1 · prochain")).to_be_visible()
    seconde.refresh_from_db()
    premiere.refresh_from_db()
    assert (premiere.statut, seconde.statut) == (StatutTournee.EN_COURS, StatutTournee.EN_COURS)

    # 4. Il livre l'urgence ; sa seconde tournée se termine.
    konan.get_by_role("button", name="Je suis arrivé").click()
    konan.get_by_label("Nom du réceptionnaire").fill("Mme Kouassi")
    konan.get_by_role("button", name="Valider la livraison").click()
    expect(konan.get_by_text("Tous les arrêts sont traités : tournée terminée.")).to_be_visible()
    livraison = Livraison.toutes_entreprises.get(chargement__tournee=seconde)
    assert livraison.statut == StatutLivraison.LIVREE

    # 5. Il reprend sa première tournée par le bandeau.
    bandeau = konan.get_by_role("navigation", name="Autres tournées aujourd'hui")
    expect(bandeau).to_contain_text(f"{premiere.numero} En cours · 1 arrêt restant")
    bandeau.get_by_role("link").click()
    expect(konan.get_by_text("Arrêt 1 · prochain")).to_be_visible()
    expect(konan.get_by_text(premiere.numero).first).to_be_visible()
