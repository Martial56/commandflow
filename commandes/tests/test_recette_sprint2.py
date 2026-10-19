"""Scénarios de recette du cahier des charges (§22) couverts par le Sprint 2.

Ils passent par les écrans, comme un utilisateur : connexion, saisie, confirmation.
"""

import datetime
import html

import pytest
from django.urls import reverse

from catalogue.tests.factories import ProduitFactory
from clients.tests.factories import AdresseFactory, ClientFactory
from commandes.models import (
    Commande,
    HistoriqueCommande,
    LigneModeleRecurrent,
    ModeleRecurrent,
    Statut,
)
from commandes.tasks import generer_recurrentes
from commandes.tests.factories import CommandeFactory
from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from noyau.contexte import activer_entreprise

pytestmark = pytest.mark.django_db
DEMAIN = datetime.date.today() + datetime.timedelta(days=1)


@pytest.fixture(autouse=True)
def sans_2fa_obligatoire(settings):
    settings.MIDDLEWARE = [
        m for m in settings.MIDDLEWARE if "DoubleAuthentificationObligatoire" not in m
    ]


def saisir(client, adresse, produit, quantite):
    return client.post(
        reverse("commande_nouvelle"),
        {
            "client": f"{adresse.client.nom} — {adresse.client.code}",
            "canal": "telephone",
            "adresse": adresse.pk,
            "date_livraison": DEMAIN.isoformat(),
            "creneau": "06h–08h",
            "produit_1": produit.pk,
            "quantite_1": quantite,
            "action": "soumettre",
        },
    )


def test_22_13_numero_unique_et_createur_conserve(client):
    """§22-13 : numéro unique ; l'identité du créateur est conservée."""
    commercial = UtilisateurFactory(roles=[Role.COMMERCIAL])
    gestionnaire = UtilisateurFactory(entreprise=commercial.entreprise, roles=[Role.GESTIONNAIRE])
    adresse = AdresseFactory(
        client__entreprise=commercial.entreprise, client__code="CLI-000001", par_defaut=True
    )
    produit = ProduitFactory(entreprise=commercial.entreprise)

    client.force_login(commercial)
    saisir(client, adresse, produit, "10")
    client.force_login(gestionnaire)
    saisir(client, adresse, produit, "20")

    premiere, seconde = Commande.toutes_entreprises.order_by("pk")
    assert premiere.numero != seconde.numero
    assert premiere.numero.startswith(f"CMD-{datetime.date.today().year}-")
    assert (premiere.cree_par, seconde.cree_par) == (commercial, gestionnaire)

    # Le créateur reste attaché même après les actions d'un autre utilisateur.
    client.post(reverse("commande_confirmer", args=[premiere.pk]))
    premiere.refresh_from_db()
    assert (premiere.statut, premiere.cree_par) == (Statut.CONFIRMEE, commercial)
    creation = HistoriqueCommande.objects.filter(pgh_obj=premiere).order_by("pgh_id").first()
    assert creation.cree_par == commercial
    page = html.unescape(
        client.get(reverse("commande_detail", args=[premiere.pk])).content.decode()
    )
    assert f"{commercial}" in page and "Commande créée" in page


def test_22_14_recurrente_en_attente_jusqu_a_confirmation(client):
    """« Une commande récurrente reste en attente jusqu'à la confirmation quotidienne. »"""
    entreprise = EntrepriseFactory(slug="mie-dore")
    adresse = AdresseFactory(client__entreprise=entreprise, par_defaut=True)
    with activer_entreprise(entreprise):
        modele = ModeleRecurrent.objects.create(
            client=adresse.client,
            adresse=adresse,
            jours_semaine=list(range(7)),
            creneau="06h–08h",
            date_debut=datetime.date(2026, 1, 1),
        )
        LigneModeleRecurrent.objects.create(
            modele=modele, produit=ProduitFactory(entreprise=entreprise), quantite=50
        )

    veille = datetime.date(2026, 10, 14)
    generer_recurrentes(maintenant=f"{veille}T18:00:00")
    commande = Commande.toutes_entreprises.get()
    assert commande.statut == Statut.A_CONFIRMER

    # Ni une nouvelle exécution ni le dépassement de l'heure limite ne la confirment.
    generer_recurrentes(maintenant=f"{veille}T20:30:00")
    commande.refresh_from_db()
    assert commande.statut == Statut.A_CONFIRMER
    assert Commande.toutes_entreprises.count() == 1

    # Seule la confirmation explicite la fait avancer.
    client.force_login(UtilisateurFactory(entreprise=entreprise, roles=[Role.COMMERCIAL]))
    client.post(f"{reverse('recurrentes_action')}?operation=confirmer&une={commande.pk}")
    commande.refresh_from_db()
    assert commande.statut == Statut.CONFIRMEE


def test_22_26_donnees_d_une_autre_entreprise_inaccessibles(client):
    """« Les données d'une entreprise ne sont pas accessibles depuis le compte d'une autre. »"""
    a, b = EntrepriseFactory(), EntrepriseFactory()
    fiche_a = ClientFactory(entreprise=a, nom="Client secret de A")
    commande_a = CommandeFactory(adresse__client=fiche_a, numero="CMD-SECRET-A")
    ProduitFactory(entreprise=a, nom="Produit secret de A")
    gestionnaire_b = UtilisateurFactory(entreprise=b, roles=[Role.GESTIONNAIRE])
    client.force_login(gestionnaire_b)

    pages = [
        reverse("ecran_clients"),
        reverse("ecran_commandes"),
        reverse("ecran_recurrentes"),
        reverse("commande_nouvelle"),
        reverse("admin:catalogue_produit_changelist"),
    ]
    for url in pages:
        contenu = client.get(url).content.decode()
        assert "secret" not in contenu and "SECRET" not in contenu, url

    for url in [
        reverse("client_detail", args=[fiche_a.pk]),
        reverse("commande_detail", args=[commande_a.pk]),
        reverse("commande_modifier", args=[commande_a.pk]),
    ]:
        assert client.get(url).status_code == 404, url
    for url in [
        reverse("client_desactiver", args=[fiche_a.pk]),
        reverse("commande_soumettre", args=[commande_a.pk]),
        reverse("commande_annuler", args=[commande_a.pk]),
    ]:
        assert client.post(url, {"motif": "intrusion"}).status_code == 404, url

    client.post(f"{reverse('recurrentes_action')}?operation=confirmer&une={commande_a.pk}")
    commande_a.refresh_from_db()
    assert commande_a.statut == Statut.BROUILLON
