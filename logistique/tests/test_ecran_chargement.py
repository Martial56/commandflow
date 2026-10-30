"""Écran du préparateur (maquette 3) : liste des arrêts, comptage, validation, écart."""

import pytest
from django.urls import reverse

from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from logistique.etat import exceptions
from logistique.models import MotifEcart, StatutChargement
from logistique.tests.factories import TourneeFactory
from logistique.tests.test_services_controle import affecter, compter, lignes, statut
from logistique.tests.test_services_tournee import DEMAIN
from noyau.contexte import activer_entreprise
from noyau.models import Validation

pytestmark = pytest.mark.django_db
R = Role
LISTE = reverse("ecran_chargement")
HTMX = {"HX-Request": "true"}


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
def chargement(entreprise, personnes):
    return affecter(TourneeFactory(entreprise=entreprise, date=DEMAIN), personnes)


@pytest.fixture
def preparateur(client, personnes):
    client.force_login(personnes.preparateur)
    return client


def url(nom, objet):
    return reverse(nom, args=[objet.pk])


# --- Accès ---------------------------------------------------------------------------------


def test_ecran_ouvert_a_la_preparation(preparateur, chargement):
    assert preparateur.get(LISTE).status_code == 200
    assert preparateur.get(url("chargement_controle", chargement)).status_code == 200


def test_le_gestionnaire_controle_un_arret_sans_la_liste(client, personnes, chargement):
    client.force_login(personnes.gestionnaire)
    assert client.get(LISTE).status_code == 403
    texte = client.get(url("chargement_controle", chargement)).content.decode()
    assert f"{reverse('ecran_tournees')}?date={DEMAIN.isoformat()}" in texte
    client.post(url("chargement_compter", lignes(chargement)[0]), {"quantite": "60"})
    assert statut(chargement) == StatutChargement.EN_VERIFICATION


@pytest.mark.parametrize("role", [R.CHAUFFEUR, R.DIRECTION, R.COMMERCIAL, R.PRODUCTION])
def test_ecran_ferme(client, entreprise, chargement, role):
    client.force_login(UtilisateurFactory(entreprise=entreprise, roles=[role]))
    assert client.get(LISTE).status_code == 403
    assert client.get(url("chargement_controle", chargement)).status_code == 403
    reponse = client.post(url("chargement_compter", lignes(chargement)[0]), {"quantite": "60"})
    assert reponse.status_code == 403
    assert statut(chargement) == StatutChargement.A_PREPARER


def test_lien_controler_depuis_l_ecran_tournees(client, personnes, chargement):
    client.force_login(personnes.gestionnaire)
    texte = client.get(reverse("ecran_tournees"), {"date": DEMAIN.isoformat()}).content.decode()
    assert url("chargement_controle", chargement) in texte


def test_isolation_entre_entreprises(client, chargement):
    with activer_entreprise(None):
        etranger = UtilisateurFactory(entreprise=EntrepriseFactory(), roles=[R.PREPARATION])
    client.force_login(etranger)
    assert client.get(url("chargement_controle", chargement)).status_code == 404
    assert chargement.tournee.numero not in client.get(LISTE, {"date": DEMAIN}).content.decode()


# --- Liste ------------------------------------------------------------------------------------


def test_liste_des_arrets_par_defaut_a_la_prochaine_date(preparateur, chargement):
    texte = preparateur.get(LISTE).content.decode()
    assert chargement.tournee.numero in texte
    assert f"1. {chargement.commande.client.nom}" in texte and "À préparer" in texte


# --- Comptage -----------------------------------------------------------------------------------


def test_compter_une_ligne_en_htmx(preparateur, chargement):
    ligne = lignes(chargement)[0]
    reponse = preparateur.post(url("chargement_compter", ligne), {"quantite": "60"}, headers=HTMX)
    texte = reponse.content.decode()
    assert 'id="controle"' in texte and "<header" not in texte
    assert "Conforme" in texte
    ligne.refresh_from_db()
    assert ligne.quantite_preparee == 60
    assert statut(chargement) == StatutChargement.EN_VERIFICATION


def test_ecart_affiche_le_motif(preparateur, chargement):
    ligne = lignes(chargement)[0]
    texte = preparateur.post(
        url("chargement_compter", ligne), {"quantite": "30"}, headers=HTMX
    ).content.decode()
    assert "Écart -30" in texte and "Motif de l&#x27;écart" in texte or "Motif de l'écart" in texte
    texte = preparateur.post(
        url("chargement_compter", ligne), {"quantite": "30", "motif": "autre"}, headers=HTMX
    ).content.decode()
    assert "Précision" in texte


def test_quantite_invalide_affiche_l_erreur(preparateur, chargement):
    texte = preparateur.post(
        url("chargement_compter", lignes(chargement)[0]), {"quantite": "abc"}, headers=HTMX
    ).content.decode()
    assert "Quantité invalide." in texte


def test_quantite_decimale_avec_virgule(preparateur, chargement):
    ligne = lignes(chargement)[0]
    preparateur.post(url("chargement_compter", ligne), {"quantite": "2,5"}, headers=HTMX)
    ligne.refresh_from_db()
    assert str(ligne.quantite_preparee) == "2.500"


# --- Actions ------------------------------------------------------------------------------------


def test_valider_passe_a_l_arret_suivant(preparateur, entreprise, personnes, chargement):
    suivant = affecter(chargement.tournee, personnes)
    for ligne, quantite in zip(lignes(chargement), ["60", "20"], strict=True):
        preparateur.post(url("chargement_compter", ligne), {"quantite": quantite})
    reponse = preparateur.post(url("chargement_valider", chargement))
    assert reponse.url == url("chargement_controle", suivant)
    assert statut(chargement) == StatutChargement.VERIFIEE_PREPARATION
    texte = preparateur.get(url("chargement_controle", chargement)).content.decode()
    assert "en attente de la réception par le chauffeur" in texte
    assert "Valider le chargement" not in texte


def test_valider_incomplet_refuse(preparateur, chargement):
    preparateur.post(url("chargement_compter", lignes(chargement)[0]), {"quantite": "60"})
    reponse = preparateur.post(url("chargement_valider", chargement), follow=True)
    assert "Compter chaque produit" in reponse.content.decode()
    assert statut(chargement) == StatutChargement.EN_VERIFICATION


def test_signaler_l_ecart_puis_completer(preparateur, personnes, chargement):
    compter(chargement, [30, 20], personnes.preparateur, motif=MotifEcart.PRODUIT_ABIME)
    texte = preparateur.get(url("chargement_controle", chargement)).content.decode()
    assert "Signaler l&#x27;écart au gestionnaire" in texte or "Signaler l'écart" in texte
    preparateur.post(url("chargement_signaler", chargement), {"commentaire": "Fournée ratée"})
    assert statut(chargement) == StatutChargement.ECART_CONSTATE
    demande = exceptions(chargement, "ecart", Validation.Decision.EN_ATTENTE).get()
    assert demande.motif == "Fournée ratée"
    texte = preparateur.get(url("chargement_controle", chargement)).content.decode()
    assert "en attente de la décision du gestionnaire" in texte
    preparateur.post(url("chargement_completer", chargement))
    assert statut(chargement) == StatutChargement.EN_VERIFICATION


def test_le_preparateur_n_a_aucun_bouton_de_reception(preparateur, personnes, chargement):
    compter(chargement, [60, 20], personnes.preparateur)
    texte = preparateur.get(url("chargement_controle", chargement)).content.decode()
    assert "Le chauffeur confirmera lui-même la réception" in texte
    assert "Je confirme" not in texte
