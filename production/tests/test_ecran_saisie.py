"""Écran de saisie de production sur téléphone (maquette 2)."""

import datetime
import html
import re

import pytest
from django.urls import reverse

from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from noyau.contexte import activer_entreprise
from production.models import BesoinProduit, SaisieProduction, StatutPlan
from production.tests.factories import BesoinProduitFactory, PlanProductionFactory

pytestmark = pytest.mark.django_db
DEMAIN = datetime.date.today() + datetime.timedelta(days=1)
ECRAN = reverse("production_saisie")


@pytest.fixture(autouse=True)
def sans_2fa_obligatoire(settings):
    settings.MIDDLEWARE = [
        m for m in settings.MIDDLEWARE if "DoubleAuthentificationObligatoire" not in m
    ]


@pytest.fixture
def plan():
    """Plan validé de la maquette 2 : baguette 980/1 400, croissant complet, pain de mie 60/150."""
    plan = PlanProductionFactory(date_livraison=DEMAIN)
    for nom, a_produire, produite in [
        ("Pain baguette", 1400, 980),
        ("Croissant", 320, 320),
        ("Pain de mie", 150, 60),
        ("Pain au chocolat", 180, 0),
    ]:
        BesoinProduitFactory(
            plan=plan,
            produit__nom=nom,
            quantite_commandee=a_produire,
            quantite_a_produire=a_produire,
            quantite_produite=produite,
        )
    with activer_entreprise(plan.entreprise):
        plan.valider(par=UtilisateurFactory(entreprise=plan.entreprise))
        plan.save()
    return plan


def atelier(plan, role=Role.PRODUCTION):
    return UtilisateurFactory(entreprise=plan.entreprise, roles=[role])


def texte(reponse):
    contenu = html.unescape(reponse.content.decode())
    return " ".join(re.sub(r"<[^>]+>", " ", contenu).split())


def besoin(plan, nom):
    return BesoinProduit.toutes_entreprises.get(plan=plan, produit__nom=nom)


def test_ecran_conforme_a_la_maquette(client, plan):
    client.force_login(atelier(plan))
    reponse = client.get(ECRAN)  # plan validé le plus proche, par défaut
    lisible = texte(reponse)
    assert reponse.status_code == 200
    assert "Plan de production · validé" in lisible and "Livraison du" in lisible
    # Premier produit encore en manque, par ordre alphabétique : Pain au chocolat.
    assert "Pain au chocolat pièce" in lisible and "À produire 180 Produit 0 Manque 180" in lisible
    assert "Chaque saisie s'ajoute aux précédentes, elle ne les remplace pas." in lisible
    assert "Autres produits du jour" in lisible
    assert "Croissant 320 / 320 produits Complet" in lisible
    assert "Pain de mie 60 / 150 produits Manque 90" in lisible
    assert "Navigation principale" not in reponse.content.decode()  # écran de terrain


def test_produit_choisi_et_quantite_proposee(client, plan):
    client.force_login(atelier(plan))
    baguette = besoin(plan, "Pain baguette")
    reponse = client.get(ECRAN, {"date": DEMAIN.isoformat(), "produit": baguette.produit_id})
    lisible = texte(reponse)
    assert "Pain baguette pièce" in lisible and "Manque 420" in lisible
    assert "quantite: '420'" in reponse.content.decode()  # proposition : ce qui manque
    assert 'value="Lot 1"' in reponse.content.decode()


@pytest.mark.parametrize("role", [Role.PRODUCTION, Role.GESTIONNAIRE])
def test_enregistrer_une_fournee(client, plan, role):
    personne = atelier(plan, role)
    client.force_login(personne)
    baguette = besoin(plan, "Pain baguette")
    reponse = client.post(
        reverse("production_saisie_enregistrer", args=[baguette.pk]),
        {"quantite": "200", "lot": "Lot 3 · four n°2", "commentaire": ""},
        follow=True,
    )
    assert "Pain baguette : fournée de 200 pièces enregistrée (Lot 3 · four n°2)." in texte(reponse)
    assert "Enregistrer 1200 pièces" in texte(reponse)  # 1 400 − 200 encore à produire
    saisie = SaisieProduction.toutes_entreprises.get()
    assert (saisie.quantite, saisie.lot, saisie.saisie_par) == (200, "Lot 3 · four n°2", personne)
    # 980 venaient du jeu d'essai sans saisie : le cumul est recalculé depuis les saisies.
    assert besoin(plan, "Pain baguette").quantite_produite == 200


def test_quantite_invalide_signalee(client, plan):
    client.force_login(atelier(plan))
    baguette = besoin(plan, "Pain baguette")
    attendus = {
        "": "Saisir une quantité.",
        "abc": "Saisir une quantité.",
        "0": "Quantité invalide pour l'unité « pièce ».",
        "12,5": "Quantité invalide pour l'unité « pièce ».",
    }
    for valeur, message in attendus.items():
        reponse = client.post(
            reverse("production_saisie_enregistrer", args=[baguette.pk]),
            {"quantite": valeur},
            follow=True,
        )
        assert message in texte(reponse), valeur
    assert not SaisieProduction.toutes_entreprises.exists()


def test_annuler_sa_saisie_depuis_le_telephone(client, plan):
    client.force_login(atelier(plan))
    baguette = besoin(plan, "Pain baguette")
    url = reverse("production_saisie_enregistrer", args=[baguette.pk])
    client.post(url, {"quantite": "4000", "lot": "Lot 1"})
    saisie = SaisieProduction.toutes_entreprises.get()
    reponse = client.post(
        reverse("production_saisie_annuler", args=[saisie.pk]),
        {"motif": "zéro de trop"},
        follow=True,
    )
    lisible = texte(reponse)
    assert "Saisie « Lot 1 » annulée." in lisible and "Annulée : « zéro de trop »" in lisible
    assert besoin(plan, "Pain baguette").quantite_produite == 0


@pytest.mark.parametrize("role", [Role.DIRECTION, Role.COMMERCIAL, Role.PREPARATION])
def test_ecran_refuse_aux_autres_roles(client, plan, role):
    client.force_login(atelier(plan, role))
    assert client.get(ECRAN).status_code == 403
    baguette = besoin(plan, "Pain baguette")
    reponse = client.post(
        reverse("production_saisie_enregistrer", args=[baguette.pk]), {"quantite": "10"}
    )
    assert reponse.status_code == 403
    assert not SaisieProduction.toutes_entreprises.exists()


def test_plan_non_valide(client):
    plan = PlanProductionFactory(date_livraison=DEMAIN)
    BesoinProduitFactory(plan=plan)
    client.force_login(atelier(plan))
    reponse = client.get(ECRAN, {"date": DEMAIN.isoformat()})
    assert "Le plan n'est pas encore validé" in texte(reponse)


def test_plan_cloture(client, plan):
    with activer_entreprise(plan.entreprise):
        plan.cloturer(par=atelier(plan))
        plan.save()
    client.force_login(atelier(plan))
    reponse = client.get(ECRAN, {"date": DEMAIN.isoformat()})
    assert "Le plan est clôturé" in texte(reponse)
    assert plan.statut == StatutPlan.CLOTURE


def test_lien_depuis_le_plan_de_production(client, plan):
    client.force_login(atelier(plan))
    reponse = client.get(reverse("ecran_production"), {"date": DEMAIN.isoformat()})
    assert "Saisir la production" in texte(reponse)


def test_besoin_d_une_autre_entreprise_introuvable(client, plan):
    ailleurs = UtilisateurFactory(entreprise=EntrepriseFactory(), roles=[Role.PRODUCTION])
    client.force_login(ailleurs)
    baguette = besoin(plan, "Pain baguette")
    reponse = client.post(
        reverse("production_saisie_enregistrer", args=[baguette.pk]), {"quantite": "10"}
    )
    assert reponse.status_code == 404
    assert "Aucun plan de production validé" in texte(client.get(ECRAN))
