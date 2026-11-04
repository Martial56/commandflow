"""Modèles de la facturation (§3.6, décisions 8.5) : contraintes, transition `annuler`,
TVA par produit, isolation entre entreprises."""

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django_fsm import TransitionNotAllowed, can_proceed, has_transition_perm

from catalogue.tests.factories import ProduitFactory
from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from facturation.models import Facture, NatureAutreDocument, StatutFacture, TypeDocument
from facturation.tests.factories import FactureFactory
from noyau.contexte import activer_entreprise

pytestmark = pytest.mark.django_db
R = Role


@pytest.fixture
def facture():
    facture = FactureFactory()
    with activer_entreprise(facture.entreprise):
        yield facture


def enregistrer_brut(facture, **champs):
    for nom, valeur in champs.items():
        setattr(facture, nom, valeur)
    with pytest.raises(IntegrityError), transaction.atomic():
        facture.save()


# --- Contraintes ---------------------------------------------------------------------


def test_numero_unique_par_entreprise(facture):
    with pytest.raises(IntegrityError), transaction.atomic():
        FactureFactory(entreprise=facture.entreprise, numero_interne=facture.numero_interne)
    with activer_entreprise(None):
        autre = FactureFactory(numero_interne=facture.numero_interne)  # autre entreprise
    assert autre.pk


def test_fne_exige_son_numero(facture):
    """Décision 8.5 : le numéro FNE est obligatoire à l'enregistrement."""
    enregistrer_brut(facture, numero_fne="")


def test_autre_document_exige_nature_et_motif(facture):
    enregistrer_brut(facture, type_document=TypeDocument.AUTRE, numero_fne="", nature="")
    facture.refresh_from_db()
    enregistrer_brut(
        facture,
        type_document=TypeDocument.AUTRE,
        nature=NatureAutreDocument.RECU,
        motif_sans_fne="",
    )
    facture.refresh_from_db()
    facture.type_document = TypeDocument.AUTRE
    facture.nature = NatureAutreDocument.RECU
    facture.motif_sans_fne = "Particulier, paiement comptant"
    facture.save()


def test_montant_ht_au_plus_le_ttc(facture):
    enregistrer_brut(facture, montant_ht=Decimal(12000))


def test_document_manquant_tant_que_le_pdf_n_est_pas_joint(facture):
    """Décision 8.5 : le PDF de la FNE peut venir plus tard, avec une alerte."""
    assert facture.document_manquant and facture.est_fne


# --- Transition « annuler » ------------------------------------------------------------


@pytest.mark.parametrize("role", [R.COMPTABILITE, R.DIRECTION])
def test_annuler_autorise(facture, role):
    personne = UtilisateurFactory(entreprise=facture.entreprise, roles=[role])
    facture.motif_annulation = "Erreur de client"
    assert has_transition_perm(facture.annuler, personne)
    facture.annuler()
    facture.save()
    facture.refresh_from_db()
    assert facture.statut == StatutFacture.ANNULEE and not facture.document_manquant


@pytest.mark.parametrize("role", [R.FACTURATION, R.GESTIONNAIRE, R.COMMERCIAL])
def test_annuler_refuse_aux_autres_roles(facture, role):
    facture.motif_annulation = "Erreur"
    personne = UtilisateurFactory(entreprise=facture.entreprise, roles=[role])
    assert not has_transition_perm(facture.annuler, personne)


def test_annuler_exige_un_motif(facture):
    assert not can_proceed(facture.annuler)


def test_annuler_refuse_au_mauvais_statut(facture):
    facture.motif_annulation = "Erreur"
    facture.annuler()
    with pytest.raises(TransitionNotAllowed):
        facture.annuler()


def test_annuler_refuse_a_une_autre_entreprise(facture):
    with activer_entreprise(None):
        etranger = UtilisateurFactory(entreprise=EntrepriseFactory(), roles=[R.DIRECTION])
    facture.motif_annulation = "Erreur"
    assert not has_transition_perm(facture.annuler, etranger)


# --- TVA par produit (décision 8.5) ----------------------------------------------------


def test_taux_de_tva_par_defaut_nul(entreprise):
    assert ProduitFactory(entreprise=entreprise).taux_tva == 0


@pytest.mark.parametrize("taux", [Decimal("-1"), Decimal("100.01")])
def test_taux_de_tva_borne(entreprise, taux):
    produit = ProduitFactory(entreprise=entreprise)
    produit.taux_tva = taux
    with activer_entreprise(entreprise):
        with pytest.raises(ValidationError):
            produit.full_clean()
        with pytest.raises(IntegrityError), transaction.atomic():
            produit.save()


# --- Isolation -----------------------------------------------------------------------


def test_isolation_entre_entreprises(facture):
    with activer_entreprise(None):
        autre = EntrepriseFactory()
    with activer_entreprise(autre):
        assert not Facture.objects.exists()
