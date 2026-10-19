import datetime

import pytest
from django.urls import reverse

from commandes.models import Canal, Statut
from commandes.tests.factories import CommandeFactory, LigneCommandeFactory
from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory

pytestmark = pytest.mark.django_db
LISTE = reverse("ecran_commandes")
HTMX = {"HX-Request": "true"}


@pytest.fixture(autouse=True)
def sans_2fa_obligatoire(settings):
    settings.MIDDLEWARE = [
        m for m in settings.MIDDLEWARE if "DoubleAuthentificationObligatoire" not in m
    ]


@pytest.fixture
def gestionnaire(client):
    utilisateur = UtilisateurFactory(roles=[Role.GESTIONNAIRE])
    client.force_login(utilisateur)
    return utilisateur


def commande(entreprise, **champs):
    return CommandeFactory(adresse__client__entreprise=entreprise, **champs)


@pytest.mark.parametrize(
    "role", [Role.DIRECTION, Role.GESTIONNAIRE, Role.COMMERCIAL, Role.FACTURATION]
)
def test_lecture_autorisee(client, role):
    client.force_login(UtilisateurFactory(roles=[role]))
    assert client.get(LISTE).status_code == 200


@pytest.mark.parametrize("role", [Role.COMPTABILITE, Role.CHAUFFEUR, Role.PRODUCTION])
def test_lecture_refusee(client, role):
    client.force_login(UtilisateurFactory(roles=[role]))
    assert client.get(LISTE).status_code == 403


def test_bouton_nouvelle_commande_selon_le_role(client):
    client.force_login(UtilisateurFactory(roles=[Role.COMMERCIAL]))
    assert "+ Nouvelle commande" in client.get(LISTE).content.decode()
    client.force_login(UtilisateurFactory(roles=[Role.FACTURATION]))
    assert "+ Nouvelle commande" not in client.get(LISTE).content.decode()


def test_colonnes_conformes_a_la_maquette(client, gestionnaire):
    cmd = commande(
        gestionnaire.entreprise,
        numero="CMD-2026-001284",
        urgente=True,
        date_livraison=datetime.date(2026, 10, 15),
        avec_ligne=False,
    )
    LigneCommandeFactory(commande=cmd, quantite=120, produit__prix_reference=250)
    LigneCommandeFactory(commande=cmd, quantite=40, produit__prix_reference=300)
    LigneCommandeFactory(commande=cmd, quantite=5, produit__prix_reference=700, retiree=True)
    contenu = client.get(LISTE).content.decode()
    for texte in [
        "Rechercher", "Date de livraison", "Canal", "Urgentes uniquement", "Toutes · 1",
        "À confirmer · 0", "En préparation · 0", "En livraison · 0", "Livrées · 0",
        "En anomalie · 0", "CMD-2026-001284", "URGENTE", "15 oct. · 06h–08h", "WhatsApp",
        "42 000", "Brouillon", "1 sur 1 commande",
    ]:  # fmt: skip
        assert texte in contenu, texte


def test_filtres(client, gestionnaire):
    entreprise = gestionnaire.entreprise
    commande(
        entreprise,
        numero="CMD-A",
        statut=Statut.A_CONFIRMER,
        adresse__client__nom="Café Le Plateau",
    )
    commande(entreprise, numero="CMD-B", urgente=True, canal=Canal.TELEPHONE)
    commande(entreprise, numero="CMD-C", date_livraison=datetime.date(2026, 12, 24))

    def numeros(**filtres):
        contenu = client.get(LISTE, filtres, headers=HTMX).content.decode()
        return {n for n in ("CMD-A", "CMD-B", "CMD-C") if n in contenu}

    assert numeros() == {"CMD-A", "CMD-B", "CMD-C"}
    assert numeros(statut="a_confirmer") == {"CMD-A"}
    assert numeros(q="plateau") == {"CMD-A"}
    assert numeros(urgentes="1") == {"CMD-B"}
    assert numeros(canal="telephone") == {"CMD-B"}
    assert numeros(date="2026-12-24") == {"CMD-C"}
    assert "Navigation principale" not in client.get(LISTE, headers=HTMX).content.decode()


def test_onglet_livrees_regroupe_livree_et_partielle(client, gestionnaire):
    entreprise = gestionnaire.entreprise
    commande(entreprise, numero="CMD-L", statut=Statut.LIVREE)
    commande(entreprise, numero="CMD-P", statut=Statut.PARTIELLEMENT_LIVREE)
    contenu = client.get(LISTE, {"statut": "livrees"}).content.decode()
    assert "Livrées · 2" in contenu and "CMD-L" in contenu and "CMD-P" in contenu


def test_pagination(client, gestionnaire):
    for i in range(30):
        commande(gestionnaire.entreprise, numero=f"CMD-2026-{i:06d}")
    assert "25 sur 30 commandes" in client.get(LISTE).content.decode()
    assert "5 sur 30 commandes" in client.get(LISTE, {"page": 2}).content.decode()


def test_isolation_entre_entreprises(client, gestionnaire):
    commande(gestionnaire.entreprise, numero="CMD-CHEZ-NOUS")
    commande(EntrepriseFactory(), numero="CMD-AILLEURS")
    contenu = client.get(LISTE).content.decode()
    assert "CMD-CHEZ-NOUS" in contenu and "CMD-AILLEURS" not in contenu
    assert "Toutes · 1" in contenu
