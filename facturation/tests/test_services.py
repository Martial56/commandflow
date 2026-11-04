"""Services de facturation (§7 `enregistrer_choix`, §14, §22-22 ; décisions 8.5)."""

import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile

from clients.models import ConditionsPaiement
from clients.tests.factories import AdresseFactory, ClientFactory
from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from facturation import services
from facturation.models import (
    Facture,
    FactureLivraison,
    NatureAutreDocument,
    StatutFacture,
    TypeDocument,
)
from facturation.services import ActionImpossible
from logistique.models import LigneLivraison, Livraison, StatutLivraison
from logistique.tests.factories import LigneLivraisonFactory
from noyau.contexte import activer_entreprise, activer_utilisateur
from noyau.models import Compteur

pytestmark = pytest.mark.django_db
R = Role


@pytest.fixture
def entreprise():
    entreprise = EntrepriseFactory()
    with activer_entreprise(entreprise):
        yield entreprise


@pytest.fixture
def adresse(entreprise):
    return AdresseFactory(client__entreprise=entreprise, par_defaut=True)


def personne(entreprise, role):
    return UtilisateurFactory(entreprise=entreprise, roles=[role])


def livree(adresse, livre=10, statut=StatutLivraison.LIVREE):
    """Livraison d'une ligne (10 × 250 FCFA commandés), au statut voulu."""
    ligne = LigneLivraisonFactory(
        livraison__chargement__tournee__entreprise=adresse.entreprise,
        livraison__chargement__commande__adresse=adresse,
        quantite_prevue=10,
    )
    livraison = ligne.livraison
    LigneLivraison.objects.filter(pk=ligne.pk).update(quantite_livree=livre)
    Livraison.objects.filter(pk=livraison.pk).update(statut=statut)
    livraison.refresh_from_db()
    return livraison


def emettre(livraisons, par, **kwargs):
    kwargs.setdefault("type_document", TypeDocument.FNE)
    kwargs.setdefault("numero_fne", "FNE-2026-0001")
    with activer_utilisateur(par):
        return services.enregistrer_choix(livraisons, **kwargs)


@pytest.fixture
def facturation(entreprise):
    return personne(entreprise, R.FACTURATION)


# --- Montants et échéance ------------------------------------------------------------


def test_montant_des_quantites_livrees(adresse):
    livraison = livree(adresse, livre=6)
    assert services.montants_livraison(livraison) == (Decimal(1500), Decimal(1500))


def test_montant_ht_selon_le_taux_du_produit(adresse):
    livraison = livree(adresse, livre=10)
    produit = LigneLivraison.objects.get(livraison=livraison).ligne_commande.produit
    produit.taux_tva = Decimal("18")
    produit.save()
    # 2 500 TTC ÷ 1,18 = 2 118,64 → 2 119 (arrondi au franc, décision 8.1).
    assert services.montants_livraison(livraison) == (Decimal(2119), Decimal(2500))


@pytest.mark.parametrize(
    ("conditions", "delai", "attendue"),
    [
        (ConditionsPaiement.COMPTANT, None, datetime.date(2026, 2, 10)),
        (ConditionsPaiement.FIN_DE_MOIS, None, datetime.date(2026, 2, 28)),
        (ConditionsPaiement.X_JOURS, 30, datetime.date(2026, 3, 12)),
    ],
)
def test_echeance_selon_le_client(entreprise, conditions, delai, attendue):
    client = ClientFactory(
        entreprise=entreprise, conditions_paiement=conditions, delai_paiement_jours=delai
    )
    assert services.echeance_par_defaut(client, datetime.date(2026, 2, 10)) == attendue


# --- Choix FNE ou autre document (§22-22) -------------------------------------------


def test_fne_pour_deux_livraisons_du_meme_client(adresse, facturation):
    premiere, seconde = livree(adresse, livre=10), livree(adresse, livre=4)
    facture = emettre([premiere, seconde], facturation)
    assert facture.numero_interne == f"FAC-{datetime.date.today().year}-000001"
    assert (facture.client, facture.montant_ttc) == (adresse.client, Decimal(3500))
    assert (facture.choix_fait_par, facture.statut) == (facturation, StatutFacture.EMISE)
    assert facture.choix_fait_le and facture.document_manquant
    assert {ligne.livraison for ligne in facture.lignes.all()} == {premiere, seconde}
    assert not services.livraisons_a_facturer().filter(pk__in=[premiere.pk, seconde.pk])
    # §22-22 : la livraison reste enregistrée indépendamment du choix.
    for livraison in (premiere, seconde):
        livraison.refresh_from_db()
        assert livraison.statut == StatutLivraison.LIVREE


def test_le_choix_ne_touche_pas_la_commande(adresse, facturation):
    livraison = livree(adresse)
    commande = livraison.commande
    statut_avant = commande.statut
    emettre([livraison], facturation)
    commande.refresh_from_db()
    assert commande.statut == statut_avant


def test_autre_document_avec_nature_et_motif(adresse, facturation):
    facture = emettre(
        [livree(adresse)],
        facturation,
        type_document=TypeDocument.AUTRE,
        nature=NatureAutreDocument.RECU,
        motif="Particulier, réglé comptant",
        numero_fne="ignoré",
    )
    assert (facture.type_document, facture.numero_fne) == (TypeDocument.AUTRE, "")
    assert facture.motif_sans_fne == "Particulier, réglé comptant"


@pytest.mark.parametrize(
    ("donnees", "message"),
    [
        ({"numero_fne": ""}, "n° de la FNE"),
        ({"type_document": TypeDocument.AUTRE, "nature": "", "motif": "x"}, "type de document"),
        (
            {"type_document": TypeDocument.AUTRE, "nature": NatureAutreDocument.RECU},
            "motif",
        ),
        ({"type_document": "inconnu"}, "Choisir"),
    ],
)
def test_pieces_exigees_selon_le_type(adresse, facturation, donnees, message):
    with pytest.raises(ActionImpossible, match=message):
        emettre([livree(adresse)], facturation, **donnees)
    assert not Facture.objects.exists()


def test_numero_fne_deja_enregistre(adresse, facturation):
    emettre([livree(adresse)], facturation, numero_fne="FNE-42")
    with pytest.raises(ActionImpossible, match="déjà enregistrée"):
        emettre([livree(adresse)], facturation, numero_fne="FNE-42")


def test_un_seul_client_par_document(entreprise, adresse, facturation):
    autre = AdresseFactory(client__entreprise=entreprise, par_defaut=True)
    with pytest.raises(ActionImpossible, match="même client"):
        emettre([livree(adresse), livree(autre)], facturation)


@pytest.mark.parametrize("statut", [StatutLivraison.ECHOUEE, StatutLivraison.EN_ATTENTE])
def test_livraison_non_livree_refusee(adresse, facturation, statut):
    with pytest.raises(ActionImpossible, match="pas encore livrée"):
        emettre([livree(adresse, statut=statut)], facturation)


def test_pas_de_double_facturation(adresse, facturation):
    livraison = livree(adresse)
    emettre([livraison], facturation)
    with pytest.raises(ActionImpossible, match="déjà facturée"):
        emettre([livraison], facturation, numero_fne="FNE-2")


def test_selection_vide(facturation):
    with pytest.raises(ActionImpossible, match="au moins une"):
        emettre([], facturation)


@pytest.mark.parametrize("role", [R.COMMERCIAL, R.COMPTABILITE, R.CHAUFFEUR])
def test_choix_refuse_aux_autres_roles(entreprise, adresse, role):
    with pytest.raises(PermissionDenied):
        emettre([livree(adresse)], personne(entreprise, role))


@pytest.mark.parametrize("role", [R.GESTIONNAIRE, R.DIRECTION])
def test_choix_autorise_aux_responsables(entreprise, adresse, role):
    assert emettre([livree(adresse)], personne(entreprise, role)).pk


# --- Document joint -------------------------------------------------------------------


def pdf(nom="fne.pdf"):
    return SimpleUploadedFile(nom, b"%PDF-1.4 fne", content_type="application/pdf")


def test_pdf_joint_a_l_emission(adresse, facturation):
    facture = emettre([livree(adresse)], facturation, fichier=pdf())
    assert facture.piece.objet == facture and not facture.document_manquant


def test_pdf_joint_plus_tard(adresse, facturation):
    facture = emettre([livree(adresse)], facturation)
    with activer_utilisateur(facturation):
        facture = services.joindre_document(facture, pdf())
    assert not facture.document_manquant


def test_document_d_un_autre_format_refuse(adresse, facturation):
    texte = SimpleUploadedFile("notes.txt", b"texte", content_type="text/plain")
    with pytest.raises(ActionImpossible, match="PDF"):
        emettre([livree(adresse)], facturation, fichier=texte)
    assert not Facture.objects.exists()


# --- Annulation et vérification (décision 8.5) --------------------------------------


@pytest.fixture
def facture(adresse, facturation):
    return emettre([livree(adresse)], facturation)


def test_annulation_rend_les_livraisons_facturables(entreprise, facture):
    with activer_utilisateur(personne(entreprise, R.COMPTABILITE)):
        services.annuler_facture(facture, "Mauvais client")
    facture.refresh_from_db()
    assert (facture.statut, facture.motif_annulation) == (StatutFacture.ANNULEE, "Mauvais client")
    livraison = facture.lignes.get().livraison
    assert livraison in services.livraisons_a_facturer()
    with activer_utilisateur(personne(entreprise, R.FACTURATION)):
        with pytest.raises(ActionImpossible, match="annulé"):
            services.joindre_document(facture, pdf())


def test_annulation_refusee(entreprise, facture, facturation):
    with activer_utilisateur(facturation):
        with pytest.raises(PermissionDenied):
            services.annuler_facture(facture, "x")
    with activer_utilisateur(personne(entreprise, R.DIRECTION)):
        with pytest.raises(ActionImpossible, match="motif"):
            services.annuler_facture(facture, " ")
    facture.refresh_from_db()
    assert facture.statut == StatutFacture.EMISE


def test_marquer_a_verifier(entreprise, facture):
    comptable = personne(entreprise, R.COMPTABILITE)
    with activer_utilisateur(comptable):
        services.marquer_a_verifier(facture, "Le client conteste 2 baguettes")
    facture.refresh_from_db()
    assert facture.a_verifier and facture.motif_verification
    with activer_utilisateur(personne(entreprise, R.GESTIONNAIRE)):
        with pytest.raises(PermissionDenied):
            services.lever_verification(facture)
    with activer_utilisateur(comptable):
        services.lever_verification(facture)
    facture.refresh_from_db()
    assert not facture.a_verifier


# --- Annulation complète et isolation ------------------------------------------------


def test_tout_est_annule_en_cas_d_erreur(adresse, facturation, monkeypatch):
    def panne(**kwargs):
        raise RuntimeError("panne")

    monkeypatch.setattr(FactureLivraison.objects, "create", panne)
    with pytest.raises(RuntimeError):
        emettre([livree(adresse)], facturation)
    assert not Facture.objects.exists()
    assert not Compteur.objects.filter(prefixe="FAC", dernier_numero__gt=0).exists()


def test_isolation_entre_entreprises(adresse):
    livraison = livree(adresse)
    with activer_entreprise(None):
        autre = EntrepriseFactory()
        etranger = UtilisateurFactory(entreprise=autre, roles=[R.FACTURATION])
    with activer_entreprise(autre):
        assert not services.livraisons_a_facturer().exists()
        with pytest.raises(ActionImpossible, match="introuvable"):
            emettre([livraison], etranger)
