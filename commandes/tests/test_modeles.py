from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from catalogue.models import Unite
from catalogue.tests.factories import ProduitFactory
from clients.tests.factories import AdresseFactory
from commandes.models import Commande, Statut, arrondir_fcfa
from commandes.tests.factories import CommandeFactory, LigneCommandeFactory
from entreprises.tests.factories import EntrepriseFactory
from noyau.contexte import activer_entreprise

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ("montant", "attendu"),
    [(Decimal("1250.5"), 1251), (Decimal("1250.49"), 1250), (Decimal("1249.5"), 1250)],
)
def test_arrondi_au_franc_moitie_vers_le_haut(montant, attendu):
    assert arrondir_fcfa(montant) == attendu


def test_prix_fige_a_la_creation_et_montant_calcule():
    ligne = LigneCommandeFactory(quantite=120, produit__prix_reference=250)
    assert (ligne.prix_unitaire, ligne.montant) == (250, 30000)

    produit = ligne.produit
    produit.prix_reference = 300
    produit.save()
    ligne.quantite = 100
    ligne.save()
    ligne.refresh_from_db()
    assert (ligne.prix_unitaire, ligne.montant) == (250, 25000)


def test_montant_au_poids_arrondi():
    ligne = LigneCommandeFactory(
        quantite=Decimal("1.333"), produit__unite=Unite.KG, produit__prix_reference=1500
    )
    assert ligne.montant == 2000  # 1999,5 → 2000


def test_montant_total_hors_lignes_retirees():
    commande = CommandeFactory(avec_ligne=False)
    LigneCommandeFactory(commande=commande, quantite=10, produit__prix_reference=250)
    LigneCommandeFactory(commande=commande, quantite=4, produit__prix_reference=300, retiree=True)
    with activer_entreprise(commande.entreprise):
        assert commande.montant_total == 2500


@pytest.mark.parametrize(
    ("unite", "quantite", "valide"),
    [
        (Unite.PIECE, Decimal("2.5"), False),
        (Unite.PIECE, 3, True),
        (Unite.KG, Decimal("2.5"), True),
    ],
)
def test_quantite_selon_l_unite(unite, quantite, valide):
    ligne = LigneCommandeFactory.build(
        commande=CommandeFactory(avec_ligne=False),
        produit=ProduitFactory(unite=unite),
        quantite=quantite,
        prix_unitaire=100,
        montant=0,
    )
    if valide:
        ligne.full_clean(exclude=["entreprise"])
    else:
        with pytest.raises(ValidationError):
            ligne.full_clean(exclude=["entreprise"])


def test_quantite_nulle_refusee_en_base():
    ligne = LigneCommandeFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        type(ligne).toutes_entreprises.filter(pk=ligne.pk).update(quantite=0)


def test_adresse_d_un_autre_client_refusee():
    commande = CommandeFactory()
    commande.adresse = AdresseFactory(client__entreprise=commande.entreprise)
    with (
        activer_entreprise(commande.entreprise),
        pytest.raises(ValidationError, match="n'appartient pas au client"),
    ):
        commande.full_clean()


def test_canal_obligatoire_hors_recurrente():
    commande = CommandeFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        Commande.toutes_entreprises.filter(pk=commande.pk).update(canal="")


def test_numero_unique_par_entreprise():
    commande = CommandeFactory(numero="CMD-2026-000001")
    with pytest.raises(IntegrityError), transaction.atomic():
        CommandeFactory(numero="CMD-2026-000001", adresse__client__entreprise=commande.entreprise)
    with activer_entreprise(None):
        autre = CommandeFactory(
            numero="CMD-2026-000001", adresse__client__entreprise=EntrepriseFactory()
        )
    assert autre.pk is not None


def test_statut_protege():
    commande = CommandeFactory()
    assert commande.statut == Statut.BROUILLON
    with pytest.raises(AttributeError):
        commande.statut = Statut.CONFIRMEE


def test_isolation_entre_entreprises():
    chez_nous = CommandeFactory()
    with activer_entreprise(None):
        CommandeFactory(adresse__client__entreprise=EntrepriseFactory())
    with activer_entreprise(chez_nous.entreprise):
        assert list(Commande.objects.all()) == [chez_nous]
