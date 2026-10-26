"""Écran Plan de production (maquette 13) et fiche atelier."""

import datetime
import html
import re
from contextlib import ExitStack, contextmanager

import pytest
from django.urls import reverse

from catalogue.tests.factories import ProduitFactory
from clients.tests.factories import AdresseFactory
from commandes import services as commandes
from commandes.models import Canal
from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from noyau.contexte import activer_entreprise, activer_utilisateur
from production import services
from production.models import BesoinProduit, PlanProduction, StatutPlan

pytestmark = pytest.mark.django_db
DEMAIN = datetime.date.today() + datetime.timedelta(days=1)
ECRAN = reverse("ecran_production")


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


@pytest.fixture
def produits(entreprise):
    return {
        nom: ProduitFactory(entreprise=entreprise, nom=nom)
        for nom in ("Pain baguette", "Croissant")
    }


def commande(entreprise, personnes, client, lignes, confirmer=True):
    with en_tant_que(entreprise, personnes[Role.COMMERCIAL]):
        adresse = AdresseFactory(client__entreprise=entreprise, client__nom=client, par_defaut=True)
        nouvelle = commandes.creer_commande(
            client=adresse.client,
            lignes=lignes,
            canal=Canal.TELEPHONE,
            date_livraison=DEMAIN,
            creneau="06h–08h",
        )
        commandes.soumettre_commande(nouvelle)
        if confirmer:
            commandes.confirmer_commande(nouvelle)
        return nouvelle


@pytest.fixture
def plan(entreprise, personnes, produits):
    baguette, croissant = produits["Pain baguette"], produits["Croissant"]
    for client, quantite in [
        ("Prosuma Riviera", 350),
        ("Hôtel Ivoire Lagune", 200),
        ("Restaurant Le Baobab", 120),
        ("Supérette Les Palmiers", 100),
        ("Café Le Plateau", 60),
        ("Boutique Chez Fatou", 40),
        ("Traiteur Saveurs d'Akwaba", 30),
    ]:
        commande(entreprise, personnes, client, [(baguette, quantite), (croissant, 10)])
    return PlanProduction.toutes_entreprises.get(entreprise=entreprise)


def page(client, personne, **parametres):
    client.force_login(personne)
    reponse = client.get(ECRAN, {"date": DEMAIN.isoformat(), **parametres})
    return reponse, html.unescape(reponse.content.decode())


def texte(contenu):
    return " ".join(re.sub(r"<[^>]+>", " ", contenu).split())


def test_ecran_conforme_a_la_maquette(client, personnes, plan):
    reponse, contenu = page(client, personnes[Role.PRODUCTION])
    assert reponse.status_code == 200
    lisible = texte(contenu)
    assert "Plan de production ·" in lisible and "Brouillon" in lisible
    assert "Regroupe 7 commandes confirmées" in lisible
    for colonne in ("Produit", "Commandé", "Marge", "À produire", "Avancement"):
        assert colonne in lisible
    assert "Pain baguette 900 0" in lisible and "À lancer" in lisible
    # Détail par commande : 5 commandes puis « + 2 autres commandes ».
    assert "Croissant · détail par commande" in lisible or "Pain baguette · détail" in lisible
    assert "Imprimer la fiche atelier" in lisible and "Valider le plan" in lisible


def test_detail_du_produit_choisi(client, personnes, plan, produits):
    _, contenu = page(client, personnes[Role.PRODUCTION], produit=produits["Pain baguette"].pk)
    lisible = texte(contenu)
    assert "Pain baguette · détail par commande" in lisible
    assert "Prosuma Riviera · 350" in lisible and "Café Le Plateau · 60" in lisible
    assert "Boutique Chez Fatou" not in lisible  # au-delà des 5 premières
    assert "+ 2 autres commandes" in lisible


def test_bandeau_des_recurrentes_a_confirmer(client, entreprise, personnes, plan, produits):
    from commandes.models import Commande, ModeleRecurrent

    attente = commande(
        entreprise, personnes, "Récurrent", [(produits["Croissant"], 5)], confirmer=False
    )
    modele = ModeleRecurrent.toutes_entreprises.create(
        entreprise=entreprise,
        client=attente.client,
        adresse=attente.adresse,
        jours_semaine=[0],
        creneau="06h–08h",
        date_debut=DEMAIN,
    )
    Commande.toutes_entreprises.filter(pk=attente.pk).update(modele_recurrent=modele)
    _, contenu = page(client, personnes[Role.PRODUCTION])
    assert "1 commande récurrente est encore à confirmer (limite 20h00)" in texte(contenu)


@pytest.mark.parametrize("role", [Role.PRODUCTION, Role.GESTIONNAIRE, Role.DIRECTION])
def test_ajuster_a_produire_en_direct(client, personnes, plan, role):
    besoin = BesoinProduit.toutes_entreprises.get(plan=plan, produit__nom="Pain baguette")
    client.force_login(personnes[role])
    reponse = client.post(
        reverse("production_ajuster", args=[besoin.pk]),
        {"a_produire": "970"},
        HTTP_HX_REQUEST="true",
    )
    lisible = texte(html.unescape(reponse.content.decode()))
    assert reponse.status_code == 200 and "+70" in lisible
    besoin.refresh_from_db()
    assert besoin.quantite_a_produire == 970


def test_ajuster_valeur_invalide(client, personnes, plan):
    besoin = BesoinProduit.toutes_entreprises.get(plan=plan, produit__nom="Pain baguette")
    client.force_login(personnes[Role.PRODUCTION])
    for valeur in ("abc", "12,5", "-3"):
        reponse = client.post(
            reverse("production_ajuster", args=[besoin.pk]), {"a_produire": valeur}
        )
        assert 'role="alert"' in reponse.content.decode()
    besoin.refresh_from_db()
    assert besoin.quantite_a_produire == 900


@pytest.mark.parametrize("role", [Role.COMMERCIAL, Role.FACTURATION, Role.CHAUFFEUR])
def test_ecran_et_actions_refuses_aux_autres_roles(client, personnes, plan, role):
    reponse, _ = page(client, personnes[role])
    assert reponse.status_code == 403
    besoin = BesoinProduit.toutes_entreprises.filter(plan=plan).first()
    assert (
        client.post(
            reverse("production_ajuster", args=[besoin.pk]), {"a_produire": "1"}
        ).status_code
        == 403
    )
    assert client.post(reverse("production_valider", args=[plan.pk])).status_code == 403


def test_valider_puis_marquer_pretes_puis_cloturer(client, entreprise, personnes, plan):
    client.force_login(personnes[Role.PRODUCTION])
    reponse = client.post(reverse("production_valider", args=[plan.pk]), follow=True)
    assert "Plan validé" in html.unescape(reponse.content.decode())
    plan.refresh_from_db()
    assert plan.statut == StatutPlan.VALIDE

    besoins = BesoinProduit.toutes_entreprises.filter(plan=plan)
    with en_tant_que(entreprise, personnes[Role.PRODUCTION]):
        for besoin in besoins:
            services.enregistrer_saisie(besoin, besoin.quantite_a_produire)
    reponse = client.post(reverse("production_pretes", args=[plan.pk]), follow=True)
    assert "7 commande(s) prête(s) au chargement." in html.unescape(reponse.content.decode())

    plan.refresh_from_db()
    assert plan.changements_apres_validation == 0  # une réservation n'est pas un changement
    client.post(reverse("production_cloturer", args=[plan.pk]))
    plan.refresh_from_db()
    assert plan.statut == StatutPlan.CLOTURE
    _, contenu = page(client, personnes[Role.PRODUCTION])
    assert 'name="a_produire"' not in contenu  # plus modifiable après clôture


def test_bandeau_production_insuffisante_et_pastille_du_menu(client, personnes, plan):
    client.force_login(personnes[Role.PRODUCTION])
    client.post(reverse("production_valider", args=[plan.pk]))
    _, contenu = page(client, personnes[Role.PRODUCTION])
    lisible = texte(contenu)
    assert "Production insuffisante : Croissant (manque 70), Pain baguette (manque 900)." in lisible
    assert 'title="Alertes">2</span>' in contenu  # pastille du menu Production


def test_bandeau_des_changements_apres_validation(client, entreprise, personnes, plan, produits):
    client.force_login(personnes[Role.PRODUCTION])
    client.post(reverse("production_valider", args=[plan.pk]))
    commande(entreprise, personnes, "Tardive", [(produits["Croissant"], 15)])
    _, contenu = page(client, personnes[Role.PRODUCTION])
    assert "Le plan a changé depuis sa validation : 1 commande ajoutée." in texte(contenu)


def test_date_sans_commande(client, personnes):
    _, contenu = page(client, personnes[Role.PRODUCTION])
    assert "Aucune commande confirmée pour le" in texte(contenu)


def test_fiche_atelier(client, personnes, plan):
    client.force_login(personnes[Role.PRODUCTION])
    reponse = client.get(reverse("production_fiche_atelier", args=[plan.pk]))
    lisible = texte(html.unescape(reponse.content.decode()))
    assert reponse.status_code == 200
    assert "Pain baguette 900" in lisible and "Croissant 70" in lisible
    assert "Lots / fours" in lisible


def test_plan_d_une_autre_entreprise_introuvable(client, plan):
    ailleurs = UtilisateurFactory(entreprise=EntrepriseFactory(), roles=[Role.PRODUCTION])
    client.force_login(ailleurs)
    assert client.get(reverse("production_fiche_atelier", args=[plan.pk])).status_code == 404
    assert client.post(reverse("production_valider", args=[plan.pk])).status_code == 404
    _, contenu = page(client, ailleurs)
    assert "Aucune commande confirmée" in texte(contenu)
