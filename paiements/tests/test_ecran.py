"""Écran Paiements et créances (maquette 16) et relevé de compte du client (§15, §22-24)."""

import datetime
import html
import re

import pytest
from django.urls import reverse

from clients.tests.factories import ClientFactory
from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from noyau.contexte import activer_entreprise, activer_utilisateur
from paiements import alertes, services
from paiements.models import Paiement
from paiements.tests.test_services import facture, payer

pytestmark = pytest.mark.django_db
R = Role
ECRAN = reverse("ecran_paiements")


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
def client_(entreprise):
    return ClientFactory(entreprise=entreprise, nom="Restaurant Le Baobab")


def texte(reponse):
    contenu = html.unescape(reponse.content.decode())
    return " ".join(re.sub(r"<[^>]+>", " ", contenu).split())


def connecter(navigateur, entreprise, role):
    utilisateur = UtilisateurFactory(entreprise=entreprise, roles=[role])
    navigateur.force_login(utilisateur)
    return utilisateur


@pytest.fixture
def factures(client_):
    """FAC ancienne (102 000, en retard) et récente (108 000, non échue)."""
    return facture(client_, 102000, echeance_dans=-12), facture(client_, 108000, echeance_dans=5)


# --- Accès ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", [R.DIRECTION, R.GESTIONNAIRE, R.COMPTABILITE])
def test_ecran_ouvert_selon_la_grille(client, entreprise, role):
    connecter(client, entreprise, role)
    reponse = client.get(ECRAN)
    assert reponse.status_code == 200 and "en préparation" not in texte(reponse)


@pytest.mark.parametrize("role", [R.FACTURATION, R.COMMERCIAL, R.CHAUFFEUR])
def test_ecran_ferme_aux_autres_roles(client, entreprise, role):
    connecter(client, entreprise, role)
    assert client.get(ECRAN).status_code == 403


# --- Créances et saisie ----------------------------------------------------------------


def test_creances_par_anciennete(client, entreprise, factures):
    connecter(client, entreprise, R.COMPTABILITE)
    page = texte(client.get(ECRAN))
    assert "Créances par ancienneté (FCFA) Client Non échu 1–30 j +30 j Total" in page
    assert "Restaurant Le Baobab 108 000 102 000 0 210 000" in page
    assert "Enregistrer un paiement" in page and "Joindre le justificatif" in page


def test_factures_ouvertes_du_client_choisi(client, entreprise, client_, factures):
    ancienne, recente = factures
    connecter(client, entreprise, R.COMPTABILITE)
    contenu = client.get(reverse("paiement_affectations"), {"client": client_.pk}).content.decode()
    page = " ".join(re.sub(r"<[^>]+>", " ", html.unescape(contenu)).split())
    assert f"{ancienne.numero_interne} · solde 102 000 en retard" in page
    assert f"{recente.numero_interne} · solde 108 000" in page
    assert contenu.index(ancienne.numero_interne) < contenu.index(recente.numero_interne)
    assert f'data-solde="102000" data-numero="{ancienne.numero_interne}"' in contenu


def test_enregistrer_un_paiement_partiel(client, entreprise, client_, factures):
    """§22-24 : un paiement partiel met à jour le solde sans marquer tout payé."""
    ancienne, recente = factures
    auteur = connecter(client, entreprise, R.COMPTABILITE)
    reponse = client.post(
        reverse("paiement_enregistrer"),
        {
            "client": client_.pk,
            "montant": "150 000",
            "mode": "mobile_money",
            "reference": "MM-778812",
            f"affecte-{ancienne.pk}": "102000",
            f"affecte-{recente.pk}": "48000",
        },
    )
    assert reponse.url == reverse("releve_client", args=[client_.pk])
    paiement = Paiement.objects.get()
    assert (paiement.montant, paiement.cree_par) == (150000, auteur)
    page = texte(client.get(reponse.url))
    assert "Paiement de Restaurant Le Baobab enregistré." in page
    assert f"{ancienne.numero_interne}" in page and "Payé" in page
    assert "Partiellement payé" in page and "Reste dû 60 000" in page
    assert f"102 000 → {ancienne.numero_interne} ; 48 000 → {recente.numero_interne}" in page


def test_erreur_de_saisie_affichee(client, entreprise, client_, factures):
    ancienne, _ = factures
    connecter(client, entreprise, R.COMPTABILITE)
    reponse = client.post(
        reverse("paiement_enregistrer"),
        {
            "client": client_.pk,
            "montant": "50000",
            "mode": "especes",
            f"affecte-{ancienne.pk}": "60000",
        },
    )
    assert reponse.url == f"{ECRAN}?client={client_.pk}"
    assert "dépasse le paiement" in texte(client.get(reponse.url))
    assert not Paiement.objects.exists()


def test_facture_d_un_autre_client_refusee(client, entreprise, client_):
    autre = facture(ClientFactory(entreprise=entreprise))
    connecter(client, entreprise, R.COMPTABILITE)
    client.post(
        reverse("paiement_enregistrer"),
        {"client": client_.pk, "montant": "1000", "mode": "especes", f"affecte-{autre.pk}": "1000"},
    )
    assert not Paiement.objects.exists()
    assert "Facture introuvable." in texte(client.get(f"{ECRAN}?client={client_.pk}"))


# --- Relevé de compte ---------------------------------------------------------------------


def test_releve_avance_et_affectation(client, entreprise, client_, factures):
    ancienne, _ = factures
    comptable = connecter(client, entreprise, R.COMPTABILITE)
    paiement = payer(client_, 150000, comptable, reference="VIR-12")
    page = texte(client.get(reverse("releve_client", args=[client_.pk])))
    assert "Reste dû 210 000" in page and "Avance 150 000" in page
    assert "Non affecté" in page and "Affecter l'avance" in page
    client.post(
        reverse("paiement_affecter", args=[paiement.pk]),
        {"facture": ancienne.pk, "montant": "102000"},
    )
    page = texte(client.get(reverse("releve_client", args=[client_.pk])))
    assert "Avance affectée." in page and "Reste dû 108 000" in page and "Avance 48 000" in page


def test_annuler_un_paiement_depuis_le_releve(client, entreprise, client_, factures):
    ancienne, _ = factures
    comptable = UtilisateurFactory(entreprise=entreprise, roles=[R.COMPTABILITE])
    paiement = payer(client_, 102000, comptable, {ancienne: 102000})
    connecter(client, entreprise, R.GESTIONNAIRE)
    page = texte(client.get(reverse("releve_client", args=[client_.pk])))
    assert "Annuler ce paiement" not in page
    assert (
        client.post(reverse("paiement_annuler", args=[paiement.pk]), {"motif": "x"}).status_code
        == 403
    )
    client.force_login(comptable)
    client.post(reverse("paiement_annuler", args=[paiement.pk]), {"motif": "Chèque rejeté"})
    page = texte(client.get(reverse("releve_client", args=[client_.pk])))
    assert "Annulé par" in page and "Chèque rejeté" in page and "Reste dû 210 000" in page


def test_lien_releve_sur_la_fiche_client(client, entreprise, client_):
    connecter(client, entreprise, R.COMPTABILITE)
    lien = reverse("releve_client", args=[client_.pk])
    assert lien in client.get(reverse("client_detail", args=[client_.pk])).content.decode()
    connecter(client, entreprise, R.FACTURATION)
    assert lien not in client.get(reverse("client_detail", args=[client_.pk])).content.decode()


# --- Pastille et isolation --------------------------------------------------------------


def test_pastille_des_factures_en_retard(client, entreprise, factures):
    utilisateur = connecter(client, entreprise, R.COMPTABILITE)
    assert alertes.nombre_alertes(utilisateur) == 1
    assert 'title="Alertes">1</span>' in client.get(ECRAN).content.decode()
    ancienne, _ = factures
    with activer_utilisateur(utilisateur):
        services.enregistrer_paiement(
            client=ancienne.client,
            montant=102000,
            mode="especes",
            affectations={ancienne: 102000},
            date=datetime.date.today(),
        )
    assert alertes.nombre_alertes(utilisateur) == 0


def test_isolation_entre_entreprises(client, client_, factures):
    with activer_entreprise(None):
        etranger = UtilisateurFactory(entreprise=EntrepriseFactory(), roles=[R.COMPTABILITE])
    client.force_login(etranger)
    assert client.get(reverse("releve_client", args=[client_.pk])).status_code == 404
    page = texte(client.get(ECRAN))
    assert "Restaurant Le Baobab" not in page and "Aucune créance en cours." in page
