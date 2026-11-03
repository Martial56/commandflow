"""Retours par les écrans (§12.3, §22-21, décision 8.4) : déclarés par le livreur sur
place ou par le gestionnaire au dépôt, décidés sur l'écran Tournées ; pastille du menu."""

import html
import re

import pytest
from django.urls import reverse

from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from logistique import alertes
from logistique.models import DecisionRetour, EtatRetour, Livraison, MotifEchec, Retour
from logistique.services import Resultat, cloturer_livraison, decider_retour, declarer_retour
from logistique.tests.factories import TourneeFactory
from logistique.tests.test_depart import demarrer, recue
from logistique.tests.test_services_tournee import DEMAIN
from logistique.tests.test_transitions import utilisateur
from noyau.contexte import activer_entreprise, activer_utilisateur

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def sans_2fa_obligatoire(settings):
    settings.MIDDLEWARE = [
        m for m in settings.MIDDLEWARE if "DoubleAuthentificationObligatoire" not in m
    ]


@pytest.fixture
def entreprise():
    entreprise = EntrepriseFactory()
    with activer_entreprise(entreprise):
        yield entreprise


@pytest.fixture
def livraison(entreprise, personnes):
    """Première commande livrée, seconde encore à faire (tournée en cours)."""
    tournee = TourneeFactory(entreprise=entreprise, date=DEMAIN)
    chargements = [recue(tournee, personnes), recue(tournee, personnes)]
    demarrer(tournee)
    livraison = Livraison.objects.get(chargement=chargements[0])
    with activer_utilisateur(tournee.chauffeur):
        quantites = dict(zip(livraison.lignes.order_by("pk"), [60, 20], strict=True))
        cloturer_livraison(
            livraison, Resultat.LIVREE, quantites=quantites, receptionnaire_nom="Mme Bamba"
        )
    return livraison


def texte(reponse):
    contenu = html.unescape(reponse.content.decode())
    return " ".join(re.sub(r"<[^>]+>", " ", contenu).split())


def premiere_ligne(livraison):
    return livraison.lignes.order_by("pk").first()


def declarer_sur_place(client, livraison, **donnees):
    valeurs = {
        "ligne": premiere_ligne(livraison).pk,
        "quantite": "5",
        "etat": EtatRetour.ABIME,
        "motif": "Baguettes écrasées",
        **donnees,
    }
    return client.post(reverse("livraison_retour", args=[livraison.pk]), valeurs)


def tableau(client):
    return client.get(reverse("ecran_tournees"), {"date": DEMAIN.isoformat()})


# --- Livreur, sur place ----------------------------------------------------------------


def test_le_livreur_declare_un_retour(client, livraison):
    client.force_login(livraison.tournee.chauffeur)
    reponse = declarer_sur_place(client, livraison)
    assert reponse.url == reverse("reception_tournee", args=[livraison.tournee.pk])
    retour = Retour.objects.get()
    assert (retour.quantite, retour.etat, retour.decision) == (5, EtatRetour.ABIME, "")
    assert retour.produit == premiere_ligne(livraison).ligne_commande.produit
    page = texte(client.get(reverse("ecran_ma_tournee")))
    assert "Retour déclaré au gestionnaire." in page and "1 retour déclaré" in page
    assert "Déclarer un retour de marchandise" in page


def test_retour_invalide_refuse(client, livraison):
    client.force_login(livraison.tournee.chauffeur)
    declarer_sur_place(client, livraison, quantite="61")
    assert not Retour.objects.exists()
    assert "Quantité retournée invalide" in texte(client.get(reverse("ecran_ma_tournee")))
    declarer_sur_place(client, livraison, ligne="999999")
    assert "Choisir le produit retourné." in texte(client.get(reverse("ecran_ma_tournee")))


def test_un_autre_chauffeur_ne_declare_pas(client, livraison):
    client.force_login(utilisateur(livraison, Role.CHAUFFEUR))
    assert declarer_sur_place(client, livraison).status_code == 403
    assert not Retour.objects.exists()


# --- Bureau : écran Tournées -------------------------------------------------------------


def test_le_gestionnaire_declare_au_depot(client, livraison, personnes):
    client.force_login(personnes.gestionnaire)
    page = texte(tableau(client))
    assert "Déclarer un retour au dépôt…" in page
    ligne = premiere_ligne(livraison)
    client.post(
        reverse("tournee_retour", args=[livraison.tournee.pk]),
        {
            "ligne": f"{livraison.pk}-{ligne.ligne_commande.produit_id}",
            "quantite": "12",
            "etat": EtatRetour.REVENDABLE,
            "motif": "Refusé à la livraison suivante",
        },
    )
    retour = Retour.objects.get()
    assert (retour.quantite, retour.cree_par) == (12, personnes.gestionnaire)
    assert "Retour enregistré." in texte(tableau(client))


def test_declaration_au_depot_d_une_autre_tournee_introuvable(client, livraison, personnes):
    autre = TourneeFactory(entreprise=livraison.entreprise, date=DEMAIN)
    client.force_login(personnes.gestionnaire)
    reponse = client.post(
        reverse("tournee_retour", args=[autre.pk]), {"ligne": f"{livraison.pk}-1"}
    )
    assert reponse.status_code == 404
    reponse = client.post(
        reverse("tournee_retour", args=[livraison.tournee.pk]), {"ligne": "n'importe quoi"}
    )
    assert reponse.status_code == 404


def test_decision_sur_l_ecran_tournees(client, livraison, personnes):
    client.force_login(livraison.tournee.chauffeur)
    declarer_sur_place(client, livraison)
    retour = Retour.objects.get()
    client.force_login(personnes.direction)
    page = texte(tableau(client))
    produit = premiere_ligne(livraison).ligne_commande.produit.nom
    assert f"Retours 5 {produit} · {livraison.commande.client.nom}" in page
    assert "abîmé, à détruire · Baguettes écrasées" in page
    for libelle in ("Remise en vente", "Destruction", "Avoir à étudier"):
        assert libelle in page
    client.post(reverse("retour_decider", args=[retour.pk]), {"decision": "destruction"})
    retour.refresh_from_db()
    assert (retour.decision, retour.decide_par) == (DecisionRetour.DESTRUCTION, personnes.direction)
    page = texte(tableau(client))
    assert f"Destruction ({personnes.direction})" in page


def test_pastille_des_retours_a_decider(client, livraison, personnes):
    client.force_login(livraison.tournee.chauffeur)
    declarer_sur_place(client, livraison)
    assert alertes.nombre_alertes(personnes.gestionnaire) == 1
    client.force_login(personnes.gestionnaire)
    contenu = tableau(client).content.decode()
    assert 'title="Alertes">1</span>' in contenu
    with activer_utilisateur(personnes.direction):
        decider_retour(Retour.objects.get(), DecisionRetour.REMISE_EN_VENTE)
    assert alertes.nombre_alertes(personnes.gestionnaire) == 0


@pytest.mark.parametrize("role", [Role.CHAUFFEUR, Role.COMMERCIAL, Role.PREPARATION])
def test_decision_refusee_hors_responsables(client, livraison, role):
    with activer_utilisateur(livraison.tournee.chauffeur):
        retour = declarer_retour(
            livraison,
            produit=premiere_ligne(livraison).ligne_commande.produit,
            quantite=5,
            motif="x",
            etat=EtatRetour.ABIME,
        )
    client.force_login(utilisateur(livraison, role))
    reponse = client.post(reverse("retour_decider", args=[retour.pk]), {"decision": "destruction"})
    assert reponse.status_code == 403
    retour.refresh_from_db()
    assert retour.decision == ""


def test_retour_d_un_echec(client, entreprise, personnes, livraison):
    """§22-21 : un retour depuis une livraison échouée."""
    seconde = Livraison.objects.exclude(pk=livraison.pk).get()
    with activer_utilisateur(seconde.tournee.chauffeur):
        cloturer_livraison(seconde, Resultat.ECHEC, motif=MotifEchec.REFUS)
    client.force_login(seconde.tournee.chauffeur)
    declarer_sur_place(client, seconde, quantite="60", etat=EtatRetour.REVENDABLE)
    assert Retour.objects.get().livraison == seconde


def test_isolation_entre_entreprises(client, livraison):
    with activer_utilisateur(livraison.tournee.chauffeur):
        retour = declarer_retour(
            livraison,
            produit=premiere_ligne(livraison).ligne_commande.produit,
            quantite=5,
            motif="x",
            etat=EtatRetour.ABIME,
        )
    with activer_entreprise(None):
        etranger = UtilisateurFactory(entreprise=EntrepriseFactory(), roles=[Role.GESTIONNAIRE])
    client.force_login(etranger)
    reponse = client.post(reverse("retour_decider", args=[retour.pk]), {"decision": "destruction"})
    assert reponse.status_code == 404
    with activer_entreprise(etranger.entreprise):
        assert alertes.nombre_alertes(etranger) == 0
