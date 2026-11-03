"""Recette du Sprint 5 (§22-19, §22-20, §22-21, §22-25) : de la tournée partie à la commande
livrée, par les écrans, chaque acteur avec son propre compte. Une livraison partielle
(accord client, reliquat livré dans une seconde tournée), un échec reporté, un retour
décidé."""

# Fixtures reprises de la recette du Sprint 4 (pytest les injecte par leur nom).
# ruff: noqa: F811

import datetime
import re

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from commandes.models import Commande, Statut
from logistique.models import (
    Chargement,
    LigneChargement,
    LigneLivraison,
    Livraison,
    MotifEchec,
    MoyenAccord,
    Reliquat,
    Retour,
    StatutLivraison,
    StatutTournee,
    Tournee,
)
from logistique.tests.test_recette_sprint4 import (  # noqa: F401
    DEMAIN,
    acteurs,
    connecter,
    entreprise,
    pretes,
    sans_2fa_obligatoire,
    texte,
)
from noyau.models import PieceJointe

pytestmark = pytest.mark.django_db
SURLENDEMAIN = DEMAIN + datetime.timedelta(days=1)


def lignes(chargement):
    return LigneChargement.toutes_entreprises.filter(chargement=chargement).select_related(
        "ligne_commande__produit"
    )


def preparer_et_partir(client, acteurs, tournee, livreur):
    """La préparatrice compte et valide chaque arrêt ; le livreur reçoit puis démarre."""
    chargements = Chargement.toutes_entreprises.filter(tournee=tournee).exclude(statut="retire")
    connecter(client, acteurs["awa"])
    for chargement in chargements:
        for ligne in lignes(chargement):
            client.post(
                reverse("chargement_compter", args=[ligne.pk]),
                {"quantite": f"{ligne.quantite_attendue.normalize():f}"},
            )
        client.post(reverse("chargement_valider", args=[chargement.pk]))
    connecter(client, acteurs[livreur])
    for chargement in chargements:
        quantites = {
            f"recu-{ligne.pk}": f"{ligne.quantite_attendue.normalize():f}"
            for ligne in lignes(chargement)
        }
        client.post(
            reverse("reception_recevoir", args=[chargement.pk]),
            {**quantites, "action": "confirmer"},
        )
    client.post(reverse("reception_demarrer", args=[tournee.pk]))
    tournee.refresh_from_db()
    assert tournee.statut == StatutTournee.EN_COURS


def livraison_de(commande):
    return Livraison.toutes_entreprises.filter(commande=commande).latest("pk")


def lignes_livraison(livraison):
    return LigneLivraison.toutes_entreprises.filter(livraison=livraison).select_related(
        "ligne_commande__produit"
    )


def saisir(client, livraison, resultat, livres=None, **donnees):
    valeurs = {}
    if livres:
        for ligne in lignes_livraison(livraison):
            valeurs[f"livre-{ligne.pk}"] = livres[ligne.ligne_commande.produit.nom]
    return client.post(
        reverse("livraison_saisie", args=[livraison.pk]),
        {"resultat": resultat, **valeurs, **donnees},
    )


def test_parcours_complet_de_la_livraison(client, entreprise, acteurs, pretes):
    baobab, palmiers = pretes

    # 1. Tournée de Konan, chargée et partie (Sprint 4).
    connecter(client, acteurs["aminata"])
    client.post(
        reverse("tournee_creer"),
        {"date": DEMAIN.isoformat(), "livreur": f"c-{acteurs['konan'].pk}"},
    )
    tournee = Tournee.toutes_entreprises.get(entreprise=entreprise)
    for commande in pretes:
        client.post(reverse("tournee_affecter"), {"tournee": tournee.pk, "commande": commande.pk})
    preparer_et_partir(client, acteurs, tournee, "konan")

    # 2. Konan arrive au Baobab : 30 croissants refusés, accord du client (§22-19, §22-20).
    connecter(client, acteurs["konan"])
    page = texte(client.get(reverse("reception_tournee", args=[tournee.pk])))
    assert "0 arrêts traités sur 2" in page and "Arrêt 1 · prochain" in page
    livraison = livraison_de(baobab)
    reponse = client.post(
        reverse("livraison_arriver", args=[livraison.pk]),
        {"latitude": "5.3600", "longitude": "-3.9900"},
    )
    assert reponse.url == reverse("livraison_saisie", args=[livraison.pk])
    photo = SimpleUploadedFile("bordereau.jpg", b"\xff\xd8\xff", content_type="image/jpeg")
    saisir(
        client,
        livraison,
        "partielle",
        {"Pain baguette": "100", "Croissant": "30"},
        motif_partielle=MotifEchec.REFUS,
        accord_nom="M. Coulibaly",
        accord_moyen=MoyenAccord.ORAL,
        receptionnaire_nom="M. Coulibaly",
        photo=photo,
    )
    livraison.refresh_from_db()
    assert livraison.statut == StatutLivraison.PARTIELLEMENT_LIVREE
    assert PieceJointe.toutes_entreprises.filter(object_id=livraison.pk).exists()
    reliquat = Reliquat.toutes_entreprises.get(commande_origine=baobab)
    assert reliquat.quantite == 30

    # Retour déclaré sur place : 5 baguettes écrasées.
    baguette = next(
        ligne
        for ligne in lignes_livraison(livraison)
        if ligne.ligne_commande.produit.nom == "Pain baguette"
    )
    client.post(
        reverse("livraison_retour", args=[livraison.pk]),
        {"ligne": baguette.pk, "quantite": "5", "etat": "abime", "motif": "Écrasées"},
    )

    # 3. Les Palmiers : client absent (§22-21) ; la tournée se termine.
    saisir(client, livraison_de(palmiers), "client_absent", commentaire="Rideau baissé")
    tournee.refresh_from_db()
    assert tournee.statut == StatutTournee.TERMINEE

    # §22-25 : Yao ne voit ni ne saisit la tournée de Konan.
    connecter(client, acteurs["yao"])
    assert client.get(reverse("livraison_saisie", args=[livraison.pk])).status_code == 403

    # 4. Au bureau : report de l'échec, nouvelle date du reliquat, décision sur le retour.
    connecter(client, acteurs["aminata"])
    page = texte(client.get(reverse("ecran_tournees"), {"date": DEMAIN.isoformat()}))
    assert "2/2 arrêts traités" in page and "Échec : client absent · Rideau baissé" in page
    assert "Reliquats à planifier 1" in page
    client.post(
        reverse("livraison_reporter", args=[livraison_de(palmiers).pk]),
        {"date": SURLENDEMAIN.isoformat(), "consigne": "Appeler avant de passer"},
    )
    palmiers.refresh_from_db()
    assert (palmiers.statut, palmiers.date_livraison) == (Statut.A_PLANIFIER, SURLENDEMAIN)
    client.post(reverse("reliquat_planifier", args=[baobab.pk]), {"date": SURLENDEMAIN.isoformat()})
    connecter(client, acteurs["ibrahim"])
    retour = Retour.toutes_entreprises.get()
    client.post(reverse("retour_decider", args=[retour.pk]), {"decision": "destruction"})
    retour.refresh_from_db()
    assert (retour.decision, retour.decide_par) == ("destruction", acteurs["ibrahim"])

    # 5. Le reliquat part avec Yao le surlendemain et il est livré : commande livrée.
    connecter(client, acteurs["aminata"])
    page = texte(client.get(reverse("ecran_tournees"), {"date": SURLENDEMAIN.isoformat()}))
    assert "Restaurant Le Baobab Reliquat" in page and "30 pièces" in page
    client.post(
        reverse("tournee_creer"),
        {"date": SURLENDEMAIN.isoformat(), "livreur": f"c-{acteurs['yao'].pk}"},
    )
    seconde = Tournee.toutes_entreprises.get(entreprise=entreprise, date=SURLENDEMAIN)
    client.post(reverse("tournee_affecter"), {"tournee": seconde.pk, "commande": baobab.pk})
    preparer_et_partir(client, acteurs, seconde, "yao")
    connecter(client, acteurs["yao"])
    livraison = livraison_de(baobab)
    assert [ligne.quantite_prevue for ligne in lignes_livraison(livraison)] == [30]
    saisir(client, livraison, "livree", {"Croissant": "30"}, receptionnaire_nom="Mme Touré")
    baobab.refresh_from_db()
    assert baobab.statut == Statut.LIVREE
    seconde.refresh_from_db()
    assert seconde.statut == StatutTournee.TERMINEE

    # La fiche commande garde toute l'histoire, avec les identités.
    connecter(client, acteurs["aminata"])
    reponse = client.get(reverse("commande_detail", args=[baobab.pk]))
    contenu, fiche = reponse.content.decode(), texte(reponse)
    for attendu in (
        "Livraison partielle (refus), avec l'accord du client",
        "Reliquat noté : 30 Croissant",
        "Reliquat planifié pour le",
        "Livraison effectuée (reçue par Mme Touré)",
        "Commande livrée : reliquat livré ou soldé",
        "Retour 5 Pain baguette : destruction",
        "Accord du client : M. Coulibaly (oral)",
    ):
        assert attendu in fiche, attendu
    assert str(acteurs["konan"]) in fiche and str(acteurs["yao"]) in fiche
    assert re.search(r'href="[^"]+">Photo du bordereau', contenu)
    assert Commande.toutes_entreprises.get(pk=palmiers.pk).statut == Statut.A_PLANIFIER
