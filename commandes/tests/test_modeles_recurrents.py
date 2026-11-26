"""Services des modèles récurrents (lot B10) : création, modification, retrait d'un produit
(ligne désactivée, Q1 a), désactivation ; une commande déjà générée ne change pas (Q2 a) ;
Direction et Gestionnaire seulement (Q3 a)."""

import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from catalogue.models import Unite
from catalogue.tests.factories import ProduitFactory
from clients.tests.factories import AdresseFactory
from commandes import services
from commandes.models import (
    Commande,
    HistoriqueModeleRecurrent,
    LigneModeleRecurrent,
    ModeleRecurrent,
    Statut,
)
from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from noyau.contexte import AutreEntreprise, activer_entreprise, activer_utilisateur

pytestmark = pytest.mark.django_db
JEUDI = datetime.date(2026, 10, 15)
LUNDI_A_SAMEDI = [0, 1, 2, 3, 4, 5]


@pytest.fixture
def entreprise():
    entreprise = EntrepriseFactory()
    with activer_entreprise(entreprise):
        yield entreprise


@pytest.fixture
def gestionnaire(entreprise):
    personne = UtilisateurFactory(entreprise=entreprise, roles=[Role.GESTIONNAIRE])
    with activer_utilisateur(personne):
        yield personne


@pytest.fixture
def produits(entreprise):
    return {
        "baguette": ProduitFactory(entreprise=entreprise, nom="Baguette", prix_reference=250),
        "croissant": ProduitFactory(entreprise=entreprise, nom="Croissant", prix_reference=300),
        "chocolat": ProduitFactory(entreprise=entreprise, nom="Pain au chocolat"),
    }


@pytest.fixture
def adresse(entreprise):
    return AdresseFactory(client__entreprise=entreprise, client__nom="Le Baobab", par_defaut=True)


def champs(lieu, produits, **valeurs):
    donnees = {
        "adresse": lieu,
        "jours_semaine": LUNDI_A_SAMEDI,
        "creneau": "06h–08h",
        "date_debut": datetime.date(2026, 10, 1),
        "date_fin": None,
        "lignes": [(produits["baguette"], 120), (produits["croissant"], 60)],
    }
    donnees.update(valeurs)
    return donnees


def creer(lieu, produits, **valeurs):
    return services.creer_modele_recurrent(client=lieu.client, **champs(lieu, produits, **valeurs))


def actives(modele):
    return {
        ligne.produit.nom: ligne.quantite
        for ligne in LigneModeleRecurrent.objects.filter(modele=modele, actif=True)
    }


# --- Création ------------------------------------------------------------------------------


def test_creation_par_le_gestionnaire(gestionnaire, adresse, produits):
    modele = creer(adresse, produits, jours_semaine=[5, 0, 1, 1])
    modele.refresh_from_db()
    assert modele.client == adresse.client and modele.actif
    assert modele.jours_semaine == [0, 1, 5]  # triés, sans doublon
    assert modele.cree_par == gestionnaire
    assert actives(modele) == {"Baguette": 120, "Croissant": 60}
    assert HistoriqueModeleRecurrent.objects.filter(pgh_obj=modele).exists()


@pytest.mark.parametrize("role", [Role.COMMERCIAL, Role.CHAUFFEUR, Role.COMPTABILITE])
def test_creation_refusee_aux_autres_roles(entreprise, adresse, produits, role):
    with activer_utilisateur(UtilisateurFactory(entreprise=entreprise, roles=[role])):
        with pytest.raises(PermissionDenied):
            creer(adresse, produits)
    assert not ModeleRecurrent.objects.exists()


@pytest.mark.parametrize(
    ("valeurs", "message"),
    [
        ({"jours_semaine": []}, "au moins un jour"),
        ({"jours_semaine": [7]}, "Jour de livraison inconnu"),
        ({"creneau": "23h–24h"}, "Créneau inconnu"),
        ({"adresse": None}, "adresse de livraison"),
        ({"lignes": []}, "au moins un produit"),
        ({"date_fin": datetime.date(2026, 9, 1)}, "La fin précède le début"),
    ],
)
def test_creation_refusee(gestionnaire, adresse, produits, valeurs, message):
    with pytest.raises(ValidationError, match=message):
        creer(adresse, produits, **valeurs)
    assert not ModeleRecurrent.objects.exists()


def test_adresse_d_un_autre_client_refusee(gestionnaire, entreprise, adresse, produits):
    autre = AdresseFactory(client__entreprise=entreprise)
    with pytest.raises(ValidationError, match="adresse active de ce client"):
        creer(adresse, produits, adresse=autre)


def test_annulation_complete_si_une_ligne_est_invalide(gestionnaire, adresse, produits):
    """Quantité décimale pour un produit à la pièce : rien n'est créé."""
    assert produits["croissant"].unite == Unite.PIECE
    lignes = [(produits["baguette"], 120), (produits["croissant"], Decimal("2.5"))]
    with pytest.raises(ValidationError, match="nombre entier"):
        creer(adresse, produits, lignes=lignes)
    assert not ModeleRecurrent.objects.exists() and not LigneModeleRecurrent.objects.exists()


def test_produit_desactive_refuse(gestionnaire, adresse, produits):
    produits["chocolat"].actif = False
    produits["chocolat"].save()
    with pytest.raises(ValidationError, match="n'est plus au catalogue"):
        creer(adresse, produits, lignes=[(produits["chocolat"], 10)])


# --- Modification -------------------------------------------------------------------------


def test_modification_retire_ajoute_et_remet_un_produit(gestionnaire, adresse, produits):
    modele = creer(adresse, produits)
    baguette, croissant, chocolat = (
        produits["baguette"],
        produits["croissant"],
        produits["chocolat"],
    )
    # Croissant retiré, pain au chocolat ajouté, baguette changée.
    services.modifier_modele_recurrent(
        modele, **champs(adresse, produits, lignes=[(baguette, 100), (chocolat, 30)])
    )
    assert actives(modele) == {"Baguette": 100, "Pain au chocolat": 30}
    retiree = LigneModeleRecurrent.objects.get(modele=modele, produit=croissant)
    assert not retiree.actif and retiree.quantite == 60  # gardée, jamais supprimée
    # Le croissant revient : même ligne, réactivée avec sa nouvelle quantité.
    services.modifier_modele_recurrent(
        modele,
        **champs(adresse, produits, jours_semaine=[0], lignes=[(baguette, 100), (croissant, 40)]),
    )
    retiree.refresh_from_db()
    assert retiree.actif and retiree.quantite == 40
    assert actives(modele) == {"Baguette": 100, "Croissant": 40}
    assert LigneModeleRecurrent.objects.filter(modele=modele).count() == 3
    modele.refresh_from_db()
    assert modele.jours_semaine == [0]


def test_modification_refusee_au_commercial(entreprise, gestionnaire, adresse, produits):
    modele = creer(adresse, produits)
    with activer_utilisateur(UtilisateurFactory(entreprise=entreprise, roles=[Role.COMMERCIAL])):
        with pytest.raises(PermissionDenied):
            services.modifier_modele_recurrent(modele, **champs(adresse, produits))
        with pytest.raises(PermissionDenied):
            services.desactiver_modele_recurrent(modele)


def test_modification_annulee_en_entier_si_invalide(gestionnaire, adresse, produits):
    modele = creer(adresse, produits)
    lignes = [(produits["baguette"], 10), (produits["croissant"], Decimal("0.5"))]
    with pytest.raises(ValidationError):
        services.modifier_modele_recurrent(
            modele, **champs(adresse, produits, creneau="08h–10h", lignes=lignes)
        )
    modele.refresh_from_db()
    assert modele.creneau == "06h–08h"
    assert actives(modele) == {"Baguette": 120, "Croissant": 60}


# --- Génération ---------------------------------------------------------------------------


def test_la_generation_ignore_les_produits_retires(gestionnaire, adresse, produits):
    modele = creer(adresse, produits)
    services.modifier_modele_recurrent(
        modele, **champs(adresse, produits, lignes=[(produits["baguette"], 120)])
    )
    rapport = services.generer_commandes_recurrentes(JEUDI)
    commande = rapport.creees[0]
    assert commande.statut == Statut.A_CONFIRMER
    assert [(lc.produit.nom, lc.quantite) for lc in commande.lignes.all()] == [("Baguette", 120)]


def test_une_commande_deja_generee_ne_change_pas(gestionnaire, adresse, produits):
    """Q2 a : le changement vaut pour les prochaines générations seulement."""
    modele = creer(adresse, produits)
    commande = services.generer_commandes_recurrentes(JEUDI).creees[0]
    services.modifier_modele_recurrent(
        modele,
        **champs(adresse, produits, creneau="08h–10h", lignes=[(produits["baguette"], 10)]),
    )
    commande = Commande.objects.get(pk=commande.pk)
    assert commande.creneau == "06h–08h"
    assert {lc.produit.nom: lc.quantite for lc in commande.lignes.all()} == {
        "Baguette": 120,
        "Croissant": 60,
    }


def test_desactivation_puis_reactivation(gestionnaire, adresse, produits):
    modele = creer(adresse, produits)
    services.desactiver_modele_recurrent(modele)
    modele.refresh_from_db()
    assert not modele.actif
    assert services.generer_commandes_recurrentes(JEUDI).creees == []
    services.reactiver_modele_recurrent(modele)
    modele.refresh_from_db()
    assert modele.actif
    assert len(services.generer_commandes_recurrentes(JEUDI).creees) == 1


def test_reactivation_refusee_sans_produit_actif(gestionnaire, adresse, produits):
    modele = creer(adresse, produits, lignes=[(produits["chocolat"], 10)])
    services.desactiver_modele_recurrent(modele)
    produits["chocolat"].actif = False
    produits["chocolat"].save()
    with pytest.raises(services.ActionImpossible, match="plus de produit actif"):
        services.reactiver_modele_recurrent(modele)


# --- Isolation ----------------------------------------------------------------------------


def test_isolation_entre_entreprises(gestionnaire, adresse, produits):
    with activer_entreprise(None):
        etranger = ProduitFactory(entreprise=EntrepriseFactory())
        autre_adresse = AdresseFactory(client__entreprise=etranger.entreprise)
    with pytest.raises(AutreEntreprise):
        creer(adresse, produits, lignes=[(etranger, 10)])
    with pytest.raises(AutreEntreprise):
        services.creer_modele_recurrent(
            client=autre_adresse.client, **champs(autre_adresse, produits)
        )
    modele = creer(adresse, produits)
    with activer_entreprise(etranger.entreprise):
        with pytest.raises(ModeleRecurrent.DoesNotExist):
            services.desactiver_modele_recurrent(modele)
