"""Paiements (§7 `affecter_paiement`, §15, §22-24 ; décisions 8.5) : solde et statut
calculés, affectations, avances, annulation, balance âgée, isolation."""

import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction

from clients.tests.factories import ClientFactory
from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from facturation.models import StatutFacture
from facturation.tests.factories import FactureFactory
from noyau.contexte import activer_entreprise, activer_utilisateur
from paiements import services
from paiements.models import AffectationPaiement, ModePaiement, Paiement
from paiements.services import ActionImpossible, StatutPaiement

pytestmark = pytest.mark.django_db
R = Role
AUJOURD_HUI = datetime.date.today()


@pytest.fixture
def entreprise():
    entreprise = EntrepriseFactory()
    with activer_entreprise(entreprise):
        yield entreprise


@pytest.fixture
def client_(entreprise):
    return ClientFactory(entreprise=entreprise, nom="Hôtel Ivoire Lagune")


@pytest.fixture
def comptable(entreprise):
    return UtilisateurFactory(entreprise=entreprise, roles=[R.COMPTABILITE])


def facture(client_, montant=100000, echeance_dans=10, **kwargs):
    return FactureFactory(
        entreprise=client_.entreprise,
        client=client_,
        montant_ht=montant,
        montant_ttc=montant,
        echeance=AUJOURD_HUI + datetime.timedelta(days=echeance_dans),
        **kwargs,
    )


def payer(client_, montant, par, affectations=None, **kwargs):
    kwargs.setdefault("mode", ModePaiement.MOBILE_MONEY)
    with activer_utilisateur(par):
        return services.enregistrer_paiement(
            client=client_, montant=montant, affectations=affectations, **kwargs
        )


# --- Solde et statut (§22-24) --------------------------------------------------------


def test_paiement_partiel_ne_solde_pas_la_facture(client_, comptable):
    """§22-24 : un paiement partiel met à jour le solde sans marquer la facture payée."""
    fac = facture(client_)
    assert (services.solde(fac), services.statut_paiement(fac)) == (
        Decimal(100000),
        StatutPaiement.NON_PAYE,
    )
    payer(client_, 40000, comptable, {fac: 40000})
    assert services.solde(fac) == 60000
    assert services.statut_paiement(fac) == StatutPaiement.PARTIEL
    payer(client_, 60000, comptable, {fac: 60000})
    assert services.solde(fac) == 0
    assert services.statut_paiement(fac) == StatutPaiement.PAYE


def test_en_retard_apres_l_echeance(client_, comptable):
    fac = facture(client_, echeance_dans=-1)
    assert services.statut_paiement(fac) == StatutPaiement.EN_RETARD
    payer(client_, 30000, comptable, {fac: 30000})
    assert services.statut_paiement(fac) == StatutPaiement.EN_RETARD  # toujours dû
    payer(client_, 70000, comptable, {fac: 70000})
    assert services.statut_paiement(fac) == StatutPaiement.PAYE


def test_facture_annulee_ne_doit_rien(client_):
    fac = facture(client_)
    type(fac).toutes_entreprises.filter(pk=fac.pk).update(statut=StatutFacture.ANNULEE)
    fac.refresh_from_db()
    assert services.solde(fac) == 0
    assert not services.factures_ouvertes(client_).exists()


# --- Affectations et avances ---------------------------------------------------------


def test_un_paiement_regle_plusieurs_factures(client_, comptable):
    ancienne, recente = facture(client_, 102000, 1), facture(client_, 108000, 5)
    assert list(services.factures_ouvertes(client_)) == [ancienne, recente]
    paiement = payer(
        client_,
        150000,
        comptable,
        {ancienne: 102000, recente: 48000},
        reference="MM-778812",
    )
    assert services.statut_paiement(ancienne) == StatutPaiement.PAYE
    assert services.statut_paiement(recente) == StatutPaiement.PARTIEL
    assert services.disponible(paiement) == 0
    assert list(services.factures_ouvertes(client_)) == [recente]
    assert paiement.reference == "MM-778812"


def test_avance_affectee_plus_tard(client_, comptable):
    """Décision 8.5 : la part non affectée reste en avance, affectable plus tard."""
    paiement = payer(client_, 50000, comptable)
    assert services.avances(client_) == 50000
    fac = facture(client_, 30000)
    with activer_utilisateur(comptable):
        services.affecter_paiement(paiement, fac, 30000)
    assert services.solde(fac) == 0 and services.avances(client_) == 20000


@pytest.mark.parametrize(
    ("montant", "affecte", "message"),
    [
        (50000, 60000, "dépasse le paiement"),
        (200000, 150000, "au plus 100000 FCFA"),
        (50000, -5, "invalide"),
    ],
)
def test_affectation_refusee(client_, comptable, montant, affecte, message):
    fac = facture(client_)
    with pytest.raises(ActionImpossible, match=message):
        payer(client_, montant, comptable, {fac: affecte})
    assert not Paiement.objects.exists()  # tout est annulé


def test_affectation_a_la_facture_d_un_autre_client(entreprise, client_, comptable):
    autre = facture(ClientFactory(entreprise=entreprise))
    with pytest.raises(ActionImpossible, match="n'est pas une facture"):
        payer(client_, 1000, comptable, {autre: 1000})


def test_affectation_a_une_facture_annulee(client_, comptable):
    fac = facture(client_)
    type(fac).toutes_entreprises.filter(pk=fac.pk).update(statut=StatutFacture.ANNULEE)
    with pytest.raises(ActionImpossible, match="annulée"):
        payer(client_, 1000, comptable, {fac: 1000})


@pytest.mark.parametrize(
    ("donnees", "message"),
    [
        ({"montant": 0}, "montant reçu"),
        ({"montant": "12,5"}, "invalide"),
        ({"montant": "1000.5"}, "sans décimale"),
        ({"mode": "troc"}, "mode"),
        ({"date": AUJOURD_HUI + datetime.timedelta(days=1)}, "futur"),
        (
            {"justificatif": SimpleUploadedFile("a.txt", b"x", content_type="text/plain")},
            "PDF ou en image",
        ),
    ],
)
def test_saisie_invalide(client_, comptable, donnees, message):
    donnees = {"montant": 1000, **donnees}
    with pytest.raises(ActionImpossible, match=message):
        payer(client_, donnees.pop("montant"), comptable, **donnees)


def test_justificatif_joint(client_, comptable):
    recu = SimpleUploadedFile("recu.pdf", b"%PDF-1.4", content_type="application/pdf")
    paiement = payer(client_, 1000, comptable, justificatif=recu)
    assert paiement.justificatif.objet == paiement


@pytest.mark.parametrize("role", [R.GESTIONNAIRE, R.DIRECTION])
def test_enregistrement_autorise(entreprise, client_, role):
    payeur = UtilisateurFactory(entreprise=entreprise, roles=[role])
    assert payer(client_, 1000, payeur).pk


@pytest.mark.parametrize("role", [R.FACTURATION, R.COMMERCIAL, R.CHAUFFEUR])
def test_enregistrement_refuse(entreprise, client_, role):
    with pytest.raises(PermissionDenied):
        payer(client_, 1000, UtilisateurFactory(entreprise=entreprise, roles=[role]))


# --- Annulation (décision 8.5) -------------------------------------------------------


def test_annulation_retablit_le_solde(entreprise, client_, comptable):
    fac = facture(client_)
    paiement = payer(client_, 100000, comptable, {fac: 100000})
    gestionnaire = UtilisateurFactory(entreprise=entreprise, roles=[R.GESTIONNAIRE])
    with activer_utilisateur(gestionnaire):
        with pytest.raises(PermissionDenied):
            services.annuler_paiement(paiement, "Chèque rejeté")
    with activer_utilisateur(comptable):
        with pytest.raises(ActionImpossible, match="motif"):
            services.annuler_paiement(paiement, " ")
        services.annuler_paiement(paiement, "Chèque rejeté")
        with pytest.raises(ActionImpossible, match="déjà annulé"):
            services.annuler_paiement(paiement, "x")
        with pytest.raises(ActionImpossible, match="annulé"):
            services.affecter_paiement(paiement, fac, 1)
    paiement.refresh_from_db()
    assert (paiement.annule, paiement.annule_par) == (True, comptable)
    assert services.solde(fac) == 100000
    assert services.statut_paiement(fac) == StatutPaiement.NON_PAYE


def test_annulation_sans_motif_refusee_en_base(client_, comptable):
    paiement = payer(client_, 1000, comptable)
    paiement.annule = True
    with pytest.raises(IntegrityError), transaction.atomic():
        paiement.save()


# --- Balance âgée (maquette 16) -------------------------------------------------------


def test_balance_agee(entreprise, client_, comptable):
    facture(client_, 372000, echeance_dans=5)
    facture(client_, 640000, echeance_dans=-12)
    facture(client_, 186000, echeance_dans=-45)
    payee = facture(ClientFactory(entreprise=entreprise), 50000, echeance_dans=-3)
    payer(payee.client, 50000, comptable, {payee: 50000})
    ((client_trouve, ligne),) = services.balance_agee()
    assert client_trouve == client_
    assert ligne == {
        "non_echu": Decimal(372000),
        "j30": Decimal(640000),
        "plus30": Decimal(186000),
        "total": Decimal(1198000),
    }


# --- Annulation complète et isolation ------------------------------------------------


def test_tout_est_annule_en_cas_d_erreur(client_, comptable, monkeypatch):
    premiere, seconde = facture(client_, 1000), facture(client_, 1000)
    creer = AffectationPaiement.objects.create
    appels = []

    def panne(**kwargs):
        if appels:
            raise RuntimeError("panne")
        appels.append(kwargs)
        return creer(**kwargs)

    monkeypatch.setattr(AffectationPaiement.objects, "create", panne)
    with pytest.raises(RuntimeError):
        payer(client_, 2000, comptable, {premiere: 1000, seconde: 1000})
    assert not Paiement.objects.exists() and not AffectationPaiement.objects.exists()
    assert services.solde(premiere) == 1000


def test_isolation_entre_entreprises(client_, comptable):
    fac = facture(client_)
    payer(client_, 1000, comptable, {fac: 1000})
    with activer_entreprise(None):
        autre = EntrepriseFactory()
        etranger = UtilisateurFactory(entreprise=autre, roles=[R.COMPTABILITE])
    with activer_entreprise(autre):
        assert not Paiement.objects.exists() and services.balance_agee() == []
        with activer_utilisateur(etranger):
            with pytest.raises(ObjectDoesNotExist):
                services.enregistrer_paiement(client=client_, montant=1, mode="especes")
