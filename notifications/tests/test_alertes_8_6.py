"""Alertes complétées du §16.1 (décision 8.6) : pastille Commandes, décision annoncée à
l'auteur, commandes non prêtes, tournée non partie, livraison en retard, facturation,
résumé du jour avec production et livraisons de la veille."""

# Fixtures reprises des tests de notifications (pytest les injecte par leur nom).
# ruff: noqa: F811

import datetime
import re

import pytest
from django.urls import reverse

from catalogue.tests.factories import ProduitFactory
from clients.tests.factories import AdresseFactory, ClientFactory
from commandes import alertes as alertes_commandes
from commandes import services as commandes
from commandes.models import Canal, Commande, Statut
from commandes.tests.factories import CommandeFactory
from comptes.models import Role
from entreprises.parametres import bornes_creneau
from facturation.tests.test_services import livree
from logistique.models import Livraison, StatutLivraison, StatutTournee, Tournee
from logistique.tests.factories import ChargementFactory, LivraisonFactory, TourneeFactory
from logistique.tests.test_services_tournee import forcer
from notifications.models import TypeNotification
from notifications.tasks import notifications_planifiees, resume_du_jour
from noyau.contexte import activer_utilisateur
from noyau.models import Validation
from paiements.tests.test_services import facture
from production import services as production
from production.models import PlanProduction
from production.tests.factories import BesoinProduitFactory, PlanProductionFactory

from .test_notifications import entreprise, personnes, recues_par  # noqa: F401

pytestmark = pytest.mark.django_db
R = Role
AUJOURD_HUI = datetime.date.today()


def a(heure, jour=AUJOURD_HUI):
    return datetime.datetime.combine(jour, datetime.time.fromisoformat(heure)).isoformat()


# --- Créneaux ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("texte", "attendu"),
    [
        ("06h–08h", (datetime.time(6), datetime.time(8))),
        ("6h30-8h", (datetime.time(6, 30), datetime.time(8))),
        ("Matin", None),
        ("25h–26h", None),
        ("", None),
    ],
)
def test_bornes_creneau(texte, attendu):
    assert bornes_creneau(texte) == attendu


# --- 1. Pastille du menu Commandes ------------------------------------------------------


def test_pastille_commandes_a_confirmer(client, entreprise, personnes):
    for statut in (Statut.A_CONFIRMER, Statut.A_CONFIRMER, Statut.CONFIRMEE):
        forcer(CommandeFactory(adresse__client__entreprise=entreprise), statut)
    assert alertes_commandes.nombre_alertes(personnes.commercial) == 2
    assert alertes_commandes.nombre_alertes(personnes.gestionnaire) == 2
    facturation = personnes.comptable  # ni confirmation, ni pastille
    assert alertes_commandes.nombre_alertes(facturation) == 0
    client.force_login(personnes.commercial)
    page = client.get(reverse("ecran_commandes")).content.decode()
    assert re.search(r'>Commandes<span[^>]*title="Alertes">2</span>', page)


# --- 2. Décision annoncée à l'auteur de la demande --------------------------------------


@pytest.mark.parametrize(("accepter", "texte"), [(True, "acceptée"), (False, "refusée")])
def test_auteur_prevenu_de_la_decision(entreprise, personnes, accepter, texte):
    commande = forcer(CommandeFactory(adresse__client__entreprise=entreprise), Statut.CONFIRMEE)
    with activer_utilisateur(personnes.gestionnaire):
        commandes.demander_annulation(commande, "Client fermé")
    demande = Validation.objects.get()
    with activer_utilisateur(personnes.direction):
        commandes.decider_validation_commande(demande, accepter=accepter)
    (decision,) = recues_par(personnes.gestionnaire, TypeNotification.DECISION)
    assert decision.texte == f"Annulation {texte} · {commande.numero} · {commande.client.nom}"
    assert decision.lien == reverse("commande_detail", args=[commande.pk])
    # Le décideur n'est pas notifié de sa propre décision.
    assert not recues_par(personnes.direction, TypeNotification.DECISION)


# --- 3. Commandes non prêtes ------------------------------------------------------------


@pytest.fixture
def plan_valide(entreprise, personnes):
    """Plan validé de demain : trois commandes de baguettes (120, 100 et 60)."""
    baguette = ProduitFactory(entreprise=entreprise, nom="Pain baguette")
    demain = AUJOURD_HUI + datetime.timedelta(days=1)
    with activer_utilisateur(personnes.commercial):
        for quantite in (120, 100, 60):
            adresse = AdresseFactory(client__entreprise=entreprise, par_defaut=True)
            commande = commandes.creer_commande(
                client=adresse.client,
                lignes=[(baguette, quantite)],
                canal=Canal.TELEPHONE,
                date_livraison=demain,
                creneau="06h–08h",
            )
            commandes.soumettre_commande(commande)
            commandes.confirmer_commande(commande)
    plan = PlanProduction.objects.get(date_livraison=demain)
    with activer_utilisateur(personnes.production):
        production.valider_plan(plan)
    return plan, plan.besoins.get(produit=baguette)


def test_commandes_non_pretes_signalees_au_gestionnaire(personnes, plan_valide):
    plan, besoin = plan_valide  # 280 baguettes commandées
    with activer_utilisateur(personnes.production):
        production.enregistrer_saisie(besoin, 150)
        resultat = production.marquer_plan_pret(plan)
    assert len(resultat.restantes) == 2
    (alerte,) = recues_par(personnes.gestionnaire, TypeNotification.PRODUCTION)
    assert alerte.texte.startswith(
        f"2 commandes du {plan.date_livraison:%d/%m} non prêtes au chargement : "
    )
    assert resultat.restantes[0].numero in alerte.texte
    assert alerte.lien.endswith(f"?date={plan.date_livraison:%Y-%m-%d}")


def test_pas_d_alerte_quand_tout_est_pret(personnes, plan_valide):
    plan, besoin = plan_valide
    with activer_utilisateur(personnes.production):
        production.enregistrer_saisie(besoin, 280)
        production.marquer_plan_pret(plan)
    assert not recues_par(personnes.gestionnaire, TypeNotification.PRODUCTION)


# --- 4. Tournée non partie au début de son premier créneau -------------------------------


def test_tournee_non_partie_au_debut_du_premier_creneau(entreprise, personnes):
    tournee = TourneeFactory(entreprise=entreprise, date=AUJOURD_HUI)
    for creneau in ("08h–10h", "06h30–08h"):
        chargement = ChargementFactory(tournee=tournee)
        Commande.toutes_entreprises.filter(pk=chargement.commande_id).update(creneau=creneau)
    notifications_planifiees(a("06:15"))
    assert not recues_par(personnes.gestionnaire, TypeNotification.LIVRAISON)
    notifications_planifiees(a("06:30"))
    notifications_planifiees(a("06:45"))  # une seule fois par tournée
    (alerte,) = recues_par(personnes.gestionnaire, TypeNotification.LIVRAISON)
    assert alerte.texte == (
        f"{tournee.numero} ({tournee.livreur}) pas encore partie, créneau 06h30–08h "
        "commencé : 2 commandes non reçues par le livreur"
    )
    assert alerte.lien == f"{reverse('ecran_tournees')}?date={AUJOURD_HUI:%Y-%m-%d}"


def test_tournee_partie_ou_d_un_autre_jour_sans_alerte(entreprise, personnes):
    partie = TourneeFactory(entreprise=entreprise, date=AUJOURD_HUI)
    ChargementFactory(tournee=partie)
    Tournee.toutes_entreprises.filter(pk=partie.pk).update(statut=StatutTournee.EN_COURS)
    ChargementFactory(tournee=TourneeFactory(entreprise=entreprise))  # demain
    notifications_planifiees(a("09:00"))
    assert not recues_par(personnes.gestionnaire, TypeNotification.LIVRAISON)


# --- 5. Livraison non traitée à la fin de son créneau -----------------------------------


def en_cours(entreprise, date=AUJOURD_HUI, creneau="06h–08h"):
    tournee = TourneeFactory(entreprise=entreprise, date=date)
    Tournee.toutes_entreprises.filter(pk=tournee.pk).update(statut=StatutTournee.EN_COURS)
    livraison = LivraisonFactory(chargement__tournee=tournee)
    Commande.toutes_entreprises.filter(pk=livraison.commande_id).update(creneau=creneau)
    return livraison


def test_livraison_en_retard_a_la_fin_du_creneau(entreprise, personnes):
    livraison = en_cours(entreprise)
    notifications_planifiees(a("07:59"))
    assert not recues_par(personnes.gestionnaire, TypeNotification.LIVRAISON)
    notifications_planifiees(a("08:00"))
    notifications_planifiees(a("08:15"))
    (alerte,) = recues_par(personnes.gestionnaire, TypeNotification.LIVRAISON)
    commande = livraison.commande
    assert alerte.texte.startswith(
        f"Livraison en retard · {commande.numero} · {commande.client.nom} : créneau 06h–08h dépassé"
    )


def test_livraison_traitee_ou_tournee_de_la_veille(entreprise, personnes):
    traitee = en_cours(entreprise)
    Livraison.toutes_entreprises.filter(pk=traitee.pk).update(statut=StatutLivraison.LIVREE)
    veille = en_cours(entreprise, date=AUJOURD_HUI - datetime.timedelta(days=1), creneau="")
    notifications_planifiees(a("09:00"))
    (alerte,) = recues_par(personnes.gestionnaire, TypeNotification.LIVRAISON)
    assert veille.commande.numero in alerte.texte  # la veille : en retard, créneau ou non


# --- 6. Facturation ---------------------------------------------------------------------


def test_facturation_prevenue_chaque_matin(entreprise):
    from comptes.tests.factories import UtilisateurFactory

    service = UtilisateurFactory(entreprise=entreprise, roles=[R.FACTURATION])
    notifications_planifiees(a("07:00"))
    assert not recues_par(service)  # rien à traiter : pas de notification
    livree(AdresseFactory(client__entreprise=entreprise))
    facture(ClientFactory(entreprise=entreprise), 1000)  # sans PDF joint
    notifications_planifiees(a("07:15", AUJOURD_HUI + datetime.timedelta(days=1)))
    notifications_planifiees(a("07:30", AUJOURD_HUI + datetime.timedelta(days=1)))
    (alerte,) = recues_par(service, TypeNotification.FACTURATION)
    assert alerte.texte == "1 livraison à facturer, 1 document sans PDF"
    assert alerte.lien == reverse("ecran_facturation")


# --- 7. Résumé du jour : production et livraisons de la veille -------------------------


def test_resume_avec_production_et_livraisons_de_la_veille(entreprise):
    assert "aucune production prévue, hier 0 livrées · 0 partielles · 0 en échec" in (
        resume_du_jour(AUJOURD_HUI)
    )
    plan = PlanProductionFactory(entreprise=entreprise, date_livraison=AUJOURD_HUI)
    BesoinProduitFactory(plan=plan, quantite_produite=100)
    BesoinProduitFactory(plan=plan, quantite_produite=40)
    BesoinProduitFactory(plan=plan, quantite_a_produire=0)  # rien à produire : ignoré
    hier = datetime.datetime.combine(
        AUJOURD_HUI - datetime.timedelta(days=1), datetime.time(10)
    ).astimezone()
    for statut in (StatutLivraison.LIVREE, StatutLivraison.LIVREE, StatutLivraison.ECHOUEE):
        livraison = en_cours(entreprise)
        Livraison.toutes_entreprises.filter(pk=livraison.pk).update(statut=statut, traitee_le=hier)
    resume = resume_du_jour(AUJOURD_HUI)
    assert "production 1/2 produits complets, hier 2 livrées · 0 partielles · 1 en échec" in (
        resume
    )
    assert len(resume) < 255
    assert PlanProduction.objects.count() == 1
