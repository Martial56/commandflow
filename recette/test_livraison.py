"""Recette §22-19 à 21 dans le navigateur : le chauffeur livre depuis son téléphone (arrivée
avec sa position, photo du bordereau, signature tracée), une livraison partielle avec
l'accord du client, un retour de marchandise et un échec motivé."""

# Fixtures reprises de la recette du chargement (pytest les injecte par leur nom).
# ruff: noqa: F811

import pytest
from django.urls import reverse
from playwright.sync_api import expect

from logistique import services as logistique
from logistique.models import (
    AccordClient,
    Chargement,
    LigneChargement,
    Livraison,
    MotifEchec,
    Preuve,
    Reliquat,
    Retour,
    StatutLivraison,
    StatutTournee,
    TypePreuve,
)
from noyau.contexte import activer_entreprise, activer_utilisateur
from recette.conftest import ABIDJAN
from recette.test_chargement import acteurs, demain, pretes  # noqa: F401

pytestmark = pytest.mark.recette
# En-tête JPEG minimal : le serveur n'analyse pas l'image, il la range en pièce jointe.
PHOTO = {"name": "bordereau.jpg", "mimeType": "image/jpeg", "buffer": b"\xff\xd8\xff\xe0"}


@pytest.fixture
def en_route(entreprise, acteurs, pretes):
    """Tournée de Konan chargée, reçue et partie (Sprint 4), par les services."""
    with activer_entreprise(entreprise):
        with activer_utilisateur(acteurs["aminata"]):
            tournee = logistique.creer_tournee(date=demain(), chauffeur=acteurs["konan"])
            for commande in pretes:
                logistique.affecter_commande(tournee, commande)
        chargements = list(Chargement.objects.filter(tournee=tournee))
        with activer_utilisateur(acteurs["awa"]):
            for chargement in chargements:
                for ligne in LigneChargement.objects.filter(chargement=chargement):
                    logistique.compter_ligne(ligne, ligne.quantite_attendue)
                logistique.valider_preparation(chargement)
        with activer_utilisateur(acteurs["konan"]):
            for chargement in chargements:
                lignes = LigneChargement.objects.filter(chargement=chargement)
                logistique.confirmer_reception(
                    chargement, {ligne.pk: ligne.quantite_attendue for ligne in lignes}
                )
            tournee.refresh_from_db()
            logistique.demarrer_tournee(tournee)
        tournee.refresh_from_db()
        assert tournee.statut == StatutTournee.EN_COURS
    return tournee


def livraison_de(commande):
    return Livraison.toutes_entreprises.get(commande=commande)


def signer(page):
    """Trace une signature à la souris dans le cadre prévu."""
    page.get_by_role("button", name="Signature").click()
    toile = page.locator("canvas")
    expect(toile).to_be_visible()
    cadre = toile.bounding_box()
    x, y = cadre["x"] + 20, cadre["y"] + cadre["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    for pas in range(1, 9):
        page.mouse.move(x + pas * 25, y + (15 if pas % 2 else -15), steps=4)
    page.mouse.up()


def test_22_19_20_21_livraisons_depuis_le_telephone(
    ouvrir, personne, entreprise, acteurs, pretes, en_route
):
    baobab, palmiers = pretes
    konan = ouvrir(acteurs["konan"], telephone=True)
    konan.goto(reverse("ecran_ma_tournee"))
    expect(konan.get_by_text("Arrêt 1 · prochain")).to_be_visible()
    expect(konan.get_by_role("heading", name="Restaurant Le Baobab")).to_be_visible()

    # 1. « Je suis arrivé » : la position du téléphone est jointe (GPS simulé à Abidjan).
    konan.get_by_role("button", name="Je suis arrivé").click()
    expect(konan.get_by_role("heading", level=1)).to_have_text("Restaurant Le Baobab")
    livraison = livraison_de(baobab)
    assert livraison.statut == StatutLivraison.SUR_PLACE
    assert (float(livraison.latitude), float(livraison.longitude)) == (
        ABIDJAN["latitude"],
        ABIDJAN["longitude"],
    )

    # 2. §22-19 et 20 : partielle, 30 croissants refusés, accord du client tracé ; preuve
    # par photo du bordereau, nom du réceptionnaire et signature tracée à l'écran.
    konan.get_by_label("Partielle").check()
    expect(konan.get_by_role("heading", name="Accord du client")).to_be_visible()
    konan.get_by_label("Croissant").fill("30")
    konan.get_by_label("Motif", exact=True).select_option(value=MotifEchec.REFUS)
    konan.get_by_label("Accord donné par").fill("M. Coulibaly")
    konan.get_by_label("Comment", exact=True).select_option(label="Oral")
    konan.locator("input[name=photo]").set_input_files(PHOTO)
    expect(konan.get_by_text("Photo prise : bordereau.jpg")).to_be_visible()
    konan.get_by_label("Nom du réceptionnaire").fill("M. Coulibaly")
    signer(konan)
    konan.get_by_role("button", name="Valider la livraison").click()
    expect(konan.get_by_text("Déjà traités")).to_be_visible()

    livraison.refresh_from_db()
    assert livraison.statut == StatutLivraison.PARTIELLEMENT_LIVREE
    assert livraison.receptionnaire_nom == "M. Coulibaly"
    types = set(
        Preuve.toutes_entreprises.filter(livraison=livraison).values_list("type", flat=True)
    )
    assert {TypePreuve.PHOTO_BORDEREAU, TypePreuve.SIGNATURE} <= types
    accord = AccordClient.toutes_entreprises.get(livraison=livraison)
    assert (accord.nom_client, accord.moyen) == ("M. Coulibaly", "oral")
    assert Reliquat.toutes_entreprises.get(commande_origine=baobab).quantite == 30

    # 3. §22-21 : retour déclaré depuis l'arrêt traité (quantité, motif, état).
    traite = (
        konan.locator("div", has_text="Restaurant Le Baobab")
        .filter(has=konan.get_by_text("Déclarer un retour de marchandise"))
        .last
    )
    traite.get_by_text("Déclarer un retour de marchandise").click()
    traite.get_by_label("Produit").select_option(label="Pain baguette (prévu 100)")
    traite.get_by_label("Quantité").fill("5")
    traite.get_by_label("État").select_option(label="Abîmé, à détruire")
    traite.get_by_label("Motif").fill("Écrasées")
    traite.get_by_role("button", name="Déclarer le retour").click()
    expect(konan.get_by_text("1 retour déclaré")).to_be_visible()
    retour = Retour.toutes_entreprises.get()
    assert (retour.quantite, retour.etat, retour.motif) == (5, "abime", "Écrasées")

    # 4. §22-21 : Les Palmiers, client absent ; l'échec est enregistré avec son motif.
    expect(konan.get_by_role("heading", name="Supérette Les Palmiers")).to_be_visible()
    konan.get_by_role("link", name="Arrêt non effectué").click()
    konan.get_by_label("Client absent").check()
    konan.get_by_label("Commentaire", exact=True).fill("Rideau baissé")
    konan.get_by_role("button", name="Enregistrer l'échec").click()
    expect(konan.get_by_text("Tous les arrêts sont traités : tournée terminée.")).to_be_visible()
    echec = livraison_de(palmiers)
    assert (echec.statut, echec.motif_echec) == (StatutLivraison.ECHOUEE, MotifEchec.CLIENT_ABSENT)
    en_route.refresh_from_db()
    assert en_route.statut == StatutTournee.TERMINEE

    # §22-25 : un autre chauffeur n'ouvre pas la saisie de cette livraison.
    yao = ouvrir(acteurs["yao"], telephone=True)
    assert yao.goto(reverse("livraison_saisie", args=[livraison.pk])).status == 403

    # 5. Au bureau : l'échec et le reliquat sont visibles, la fiche garde les preuves.
    bureau = ouvrir(acteurs["aminata"])
    bureau.goto(f"{reverse('ecran_tournees')}?date={demain().isoformat()}")
    expect(bureau.get_by_text("Échec : client absent · Rideau baissé")).to_be_visible()
    expect(bureau.get_by_role("heading", name="Reliquats à planifier")).to_be_visible()
    bureau.goto(reverse("commande_detail", args=[baobab.pk]))
    for attendu in (
        "Livraison partielle (refus), avec l'accord du client",
        "Reliquat noté : 30 Croissant",
        "Accord du client : M. Coulibaly (oral)",
        "Konan K.",
    ):
        expect(bureau.locator("main")).to_contain_text(attendu)
    # La photo s'ouvre par un lien signé, propre à l'utilisatrice connectée.
    lien = bureau.get_by_role("link", name="Photo du bordereau")
    assert bureau.request.get(lien.get_attribute("href")).status == 200
