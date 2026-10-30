"""Recette du Sprint 4 (§22-17, §22-18, §22-25) : de « prête » au départ de la tournée,
par les écrans, chaque acteur avec son propre compte."""

import datetime
import html
import re

import pytest
from django.urls import reverse

from catalogue.tests.factories import ProduitFactory
from clients.tests.factories import AdresseFactory
from commandes import services as commandes
from commandes.models import Canal, Commande, Statut
from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from logistique.models import (
    Chargement,
    LigneChargement,
    Livraison,
    StatutChargement,
    StatutTournee,
    Tournee,
)
from noyau.contexte import activer_entreprise, activer_utilisateur
from noyau.models import Validation
from production import services as production
from production.models import PlanProduction

pytestmark = pytest.mark.django_db
DEMAIN = datetime.date.today() + datetime.timedelta(days=1)


@pytest.fixture(autouse=True)
def sans_2fa_obligatoire(settings):
    settings.MIDDLEWARE = [
        m for m in settings.MIDDLEWARE if "DoubleAuthentificationObligatoire" not in m
    ]


def texte(reponse):
    contenu = html.unescape(reponse.content.decode())
    return " ".join(re.sub(r"<[^>]+>", " ", contenu).split())


@pytest.fixture
def entreprise():
    return EntrepriseFactory()


@pytest.fixture
def acteurs(entreprise):
    roles = {
        "aminata": Role.GESTIONNAIRE,
        "ibrahim": Role.DIRECTION,
        "fatou": Role.COMMERCIAL,
        "awa": Role.PREPARATION,
        "konan": Role.CHAUFFEUR,
        "yao": Role.CHAUFFEUR,
    }
    return {nom: UtilisateurFactory(entreprise=entreprise, roles=[r]) for nom, r in roles.items()}


@pytest.fixture
def pretes(entreprise, acteurs):
    """Deux commandes du lendemain, produites et prêtes au chargement (Sprint 3)."""
    baguette = ProduitFactory(entreprise=entreprise, nom="Pain baguette")
    croissant = ProduitFactory(entreprise=entreprise, nom="Croissant")
    with activer_entreprise(entreprise):
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
                    date_livraison=DEMAIN,
                    creneau="06h–08h",
                )
                commandes.soumettre_commande(commande)
            with activer_utilisateur(acteurs["aminata"]):
                commandes.confirmer_commande(commande)
            resultat.append(commande)
        with activer_utilisateur(acteurs["aminata"]):
            plan = PlanProduction.objects.get(date_livraison=DEMAIN)
            production.valider_plan(plan)
            for besoin in plan.besoins.all():
                production.enregistrer_saisie(besoin, besoin.quantite_a_produire)
            production.marquer_plan_pret(plan)
        assert {c.statut for c in Commande.objects.all()} == {Statut.PRETE_CHARGEMENT}
    return resultat


def lignes(chargement):
    return LigneChargement.toutes_entreprises.filter(chargement=chargement).select_related(
        "ligne_commande__produit"
    )


def connecter(client, personne):
    client.force_login(personne)
    return client


def test_parcours_complet_du_chargement_au_depart(client, entreprise, acteurs, pretes):
    baobab, palmiers = pretes
    date = {"date": DEMAIN.isoformat()}

    # 1. La gestionnaire crée la tournée de Konan et y affecte les deux commandes.
    connecter(client, acteurs["aminata"])
    client.post(reverse("tournee_creer"), {**date, "livreur": f"c-{acteurs['konan'].pk}"})
    tournee = Tournee.toutes_entreprises.get(entreprise=entreprise)
    for commande in pretes:
        client.post(reverse("tournee_affecter"), {"tournee": tournee.pk, "commande": commande.pk})
    page = texte(client.get(reverse("ecran_tournees"), date))
    assert "À affecter 0" in page and "0/2 vérifiées" in page

    # 2. §22-17 : la préparatrice compte ; Le Baobab conforme, Les Palmiers avec un écart.
    connecter(client, acteurs["awa"])
    assert "1. Restaurant Le Baobab" in texte(client.get(reverse("ecran_chargement")))
    chargements = {c.commande_id: c for c in Chargement.toutes_entreprises.filter(tournee=tournee)}
    for commande, croissants in ((baobab, "60"), (palmiers, "30")):
        chargement = chargements[commande.pk]
        for ligne in lignes(chargement):
            nom = ligne.ligne_commande.produit.nom
            quantite = croissants if nom == "Croissant" else "100"
            client.post(
                reverse("chargement_compter", args=[ligne.pk]),
                {"quantite": quantite, "motif": "production_insuffisante"},
            )
    client.post(reverse("chargement_valider", args=[chargements[baobab.pk].pk]))
    client.post(
        reverse("chargement_signaler", args=[chargements[palmiers.pk].pk]),
        {"commentaire": "30 croissants manquants"},
    )

    # Le chauffeur voit la commande vérifiée ; le départ est bloqué (§22-18).
    connecter(client, acteurs["konan"])
    page = texte(client.get(reverse("ecran_ma_tournee")))
    assert "Restaurant Le Baobab" in page and "Écart : -30 Croissant" in page
    assert "Démarrer la tournée · 2 commandes restantes" in page

    # §22-25 : Yao ne voit pas la tournée de Konan.
    connecter(client, acteurs["yao"])
    assert tournee.numero not in texte(client.get(reverse("ecran_ma_tournee")))
    assert client.get(reverse("reception_tournee", args=[tournee.pk])).status_code == 403

    # 3. La direction accepte l'écart : les 30 croissants chargés font foi (8.3).
    connecter(client, acteurs["ibrahim"])
    demande = Validation.toutes_entreprises.get(decision=Validation.Decision.EN_ATTENTE)
    client.post(reverse("chargement_decider", args=[demande.pk]), {"decision": "accepter"})

    # 4. Konan confirme lui-même chaque commande, puis démarre la tournée.
    connecter(client, acteurs["konan"])
    for commande, croissants in ((baobab, "60"), (palmiers, "30")):
        chargement = chargements[commande.pk]
        quantites = {}
        for ligne in lignes(chargement):
            nom = ligne.ligne_commande.produit.nom
            quantites[f"recu-{ligne.pk}"] = croissants if nom == "Croissant" else "100"
        client.post(
            reverse("reception_recevoir", args=[chargement.pk]),
            {**quantites, "action": "confirmer"},
        )
    tournee.refresh_from_db()
    assert tournee.statut == StatutTournee.DEPART_AUTORISE
    client.post(reverse("reception_demarrer", args=[tournee.pk]))

    # 5. Cascade : tournée partie, commandes en livraison, une livraison par chargement.
    tournee.refresh_from_db()
    assert tournee.statut == StatutTournee.EN_COURS and tournee.depart_le
    for commande in pretes:
        commande.refresh_from_db()
        assert commande.statut == Statut.EN_LIVRAISON
    assert Livraison.toutes_entreprises.filter(chargement__tournee=tournee).count() == 2

    # §22-18 : heure, identités et écart conservés, visibles sur la fiche commande.
    chargement = Chargement.toutes_entreprises.get(pk=chargements[palmiers.pk].pk)
    assert chargement.statut == StatutChargement.RECUE_CHAUFFEUR
    assert (chargement.verifie_par, chargement.recu_par) == (acteurs["awa"], acteurs["konan"])
    connecter(client, acteurs["aminata"])
    fiche = texte(client.get(reverse("commande_detail", args=[palmiers.pk])))
    for attendu in (
        f"Affectée à la tournée {tournee.numero}",
        "Écart constaté au chargement",
        "Exception pour l'écart au chargement acceptée",
        "Réception confirmée par le livreur",
        "Départ de la tournée : en livraison",
    ):
        assert attendu in fiche, attendu
    assert str(acteurs["awa"]) in fiche and str(acteurs["konan"]) in fiche
    # Tournée partie : plus aucune modification possible depuis l'écran.
    page = texte(client.get(reverse("ecran_tournees"), date))
    assert "En cours" in page and "Retirer" not in page
