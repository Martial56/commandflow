"""Recette du Sprint 3 (§22) : production groupée, de la confirmation à « prête »."""

import datetime
import html
import re
from contextlib import ExitStack, contextmanager

import pytest
from django.core.management import call_command
from django.urls import reverse

from catalogue.tests.factories import ProduitFactory
from clients.tests.factories import AdresseFactory
from commandes import services as commandes
from commandes.models import Canal, Commande, Statut
from commandes.tests.factories import CommandeFactory, LigneCommandeFactory
from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from noyau.contexte import activer_entreprise, activer_utilisateur
from production.models import BesoinProduit, PlanProduction, SaisieProduction

pytestmark = pytest.mark.django_db
DEMAIN = datetime.date.today() + datetime.timedelta(days=1)


@pytest.fixture(autouse=True)
def sans_2fa_obligatoire(settings):
    settings.MIDDLEWARE = [
        m for m in settings.MIDDLEWARE if "DoubleAuthentificationObligatoire" not in m
    ]


@pytest.fixture
def entreprise():
    return EntrepriseFactory()


@pytest.fixture
def personnes(entreprise):
    return {role: UtilisateurFactory(entreprise=entreprise, roles=[role]) for role in Role}


@contextmanager
def en_tant_que(entreprise, personne):
    with ExitStack() as pile:
        pile.enter_context(activer_entreprise(entreprise))
        pile.enter_context(activer_utilisateur(personne))
        yield personne


def texte(reponse):
    contenu = html.unescape(reponse.content.decode())
    return " ".join(re.sub(r"<[^>]+>", " ", contenu).split())


def test_parcours_complet_de_la_confirmation_a_prete(client, entreprise, personnes):
    """Confirmation → plan (regroupement) → validation → saisies → écarts → prêtes."""
    baguette = ProduitFactory(entreprise=entreprise, nom="Pain baguette")
    croissant = ProduitFactory(entreprise=entreprise, nom="Croissant")

    # 1. Le Commercial saisit et confirme trois commandes pour demain (écran).
    client.force_login(personnes[Role.COMMERCIAL])
    for nom, lignes in [
        ("Restaurant Le Baobab", [(baguette, 120), (croissant, 40)]),
        ("Hôtel Ivoire Lagune", [(baguette, 200)]),
        ("Café Le Plateau", [(baguette, 60), (croissant, 30)]),
    ]:
        with en_tant_que(entreprise, personnes[Role.COMMERCIAL]):
            adresse = AdresseFactory(
                client__entreprise=entreprise, client__nom=nom, par_defaut=True
            )
            commande = commandes.creer_commande(
                client=adresse.client,
                lignes=lignes,
                canal=Canal.TELEPHONE,
                date_livraison=DEMAIN,
                creneau="06h–08h",
            )
            commandes.soumettre_commande(commande)
        client.post(reverse("commande_confirmer", args=[commande.pk]))

    # 2. §22-15 (regroupement) : un plan par date, quantités regroupées par produit.
    plan = PlanProduction.toutes_entreprises.get(entreprise=entreprise)
    besoins = {
        b.produit.nom: b.quantite_commandee
        for b in BesoinProduit.toutes_entreprises.filter(plan=plan).select_related("produit")
    }
    assert besoins == {"Pain baguette": 380, "Croissant": 70}

    # 3. Le responsable de production ajuste, valide le plan, l'atelier saisit (écrans).
    client.force_login(personnes[Role.PRODUCTION])
    besoin_baguette = BesoinProduit.toutes_entreprises.get(plan=plan, produit=baguette)
    client.post(reverse("production_ajuster", args=[besoin_baguette.pk]), {"a_produire": "400"})
    client.post(reverse("production_valider", args=[plan.pk]))
    assert set(Commande.toutes_entreprises.values_list("statut", flat=True)) == {
        Statut.EN_PREPARATION
    }
    saisir = reverse("production_saisie_enregistrer", args=[besoin_baguette.pk])
    client.post(saisir, {"quantite": "200", "lot": "Lot 1"})
    client.post(saisir, {"quantite": "130", "lot": "Lot 2"})
    besoin_croissant = BesoinProduit.toutes_entreprises.get(plan=plan, produit=croissant)
    client.post(
        reverse("production_saisie_enregistrer", args=[besoin_croissant.pk]), {"quantite": "70"}
    )

    # §22-15 : saisies cumulées sans écraser les précédentes.
    assert SaisieProduction.toutes_entreprises.filter(besoin=besoin_baguette).count() == 2
    besoin_baguette.refresh_from_db()
    assert besoin_baguette.quantite_produite == 330

    # 4. §22-16 : le responsable visualise les écarts (nécessaire, produit, disponible).
    reponse = client.get(
        reverse("ecran_production"), {"date": DEMAIN.isoformat(), "produit": baguette.pk}
    )
    lisible = texte(reponse)
    assert "Pain baguette 380 +20" in lisible and "Manque 70" in lisible
    assert "Commandé 380 À produire 400 Produit 330 Manque 70 Réservé 0 Disponible 330" in lisible
    assert "Production insuffisante : Pain baguette (manque 70)." in lisible

    # 5. Tout le plan : prêtes dans la limite du disponible (330 baguettes pour 380).
    client.post(reverse("production_pretes", args=[plan.pk]))
    pretes = Commande.toutes_entreprises.filter(statut=Statut.PRETE_CHARGEMENT)
    restantes = Commande.toutes_entreprises.filter(statut=Statut.EN_PREPARATION)
    assert (pretes.count(), restantes.count()) == (2, 1)
    besoin_baguette.refresh_from_db()
    assert besoin_baguette.quantite_disponible == 330 - besoin_baguette.quantite_reservee >= 0


def test_rattrapage_des_commandes_confirmees(entreprise):
    """Commandes confirmées avant le Sprint 3 : intégrées au plan, sans doublon."""
    anciennes = [
        CommandeFactory(adresse__client__entreprise=entreprise, statut=Statut.CONFIRMEE)
        for _ in range(2)
    ]
    for ancienne in anciennes:
        LigneCommandeFactory(commande=ancienne, quantite=10)
    a_confirmer = CommandeFactory(adresse__client__entreprise=entreprise, statut=Statut.A_CONFIRMER)

    call_command("integrer_commandes_confirmees", "--entreprise", entreprise.slug)
    call_command("integrer_commandes_confirmees")  # relance : rien de plus

    statuts = dict(Commande.toutes_entreprises.values_list("pk", "statut"))
    assert {statuts[c.pk] for c in anciennes} == {Statut.A_PLANIFIER}
    assert statuts[a_confirmer.pk] == Statut.A_CONFIRMER
    assert PlanProduction.toutes_entreprises.filter(entreprise=entreprise).count() == 1
