"""Écran de réception du livreur (maquette 4) et recette §22-25."""

import pytest
from django.urls import reverse

from commandes.models import Statut
from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from logistique.etat import exceptions
from logistique.models import StatutChargement, StatutTournee
from logistique.tests.factories import PrestataireFactory, TourneeFactory
from logistique.tests.test_services_controle import affecter, lignes, statut, verifier
from logistique.tests.test_services_tournee import DEMAIN
from noyau.contexte import activer_entreprise
from noyau.models import Validation

pytestmark = pytest.mark.django_db
R = Role
ECRAN = reverse("ecran_ma_tournee")


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
def tournee(entreprise, personnes):
    return TourneeFactory(entreprise=entreprise, date=DEMAIN)


@pytest.fixture
def chauffeur(client, tournee):
    client.force_login(tournee.chauffeur)
    return client


def recevoir(client, chargement, quantites, **donnees):
    valeurs = {
        f"recu-{ligne.pk}": q for ligne, q in zip(lignes(chargement), quantites, strict=True)
    }
    return client.post(reverse("reception_recevoir", args=[chargement.pk]), {**valeurs, **donnees})


# --- Accès et §22-25 --------------------------------------------------------------------------


def test_arrivee_sur_sa_tournee(chauffeur, tournee, personnes):
    verifier(affecter(tournee, personnes), personnes)
    texte = chauffeur.get(ECRAN).content.decode()
    assert f"Tournée {tournee.numero}" in texte
    assert "Je confirme avoir reçu cette commande" in texte
    assert "Préparée par" in texte


def test_sans_tournee(client, entreprise):
    client.force_login(UtilisateurFactory(entreprise=entreprise, roles=[R.CHAUFFEUR]))
    assert "Aucune tournée ne vous est affectée" in client.get(ECRAN).content.decode()


def test_recette_22_25_un_chauffeur_ne_voit_pas_la_tournee_d_un_autre(client, tournee, personnes):
    chargement = verifier(affecter(tournee, personnes), personnes)
    autre = personnes.autre_chauffeur
    client.force_login(autre)
    assert tournee.numero not in client.get(ECRAN).content.decode()
    assert client.get(reverse("reception_tournee", args=[tournee.pk])).status_code == 403
    assert recevoir(client, chargement, ["60", "20"]).status_code == 403
    demarrer = client.post(reverse("reception_demarrer", args=[tournee.pk]))
    assert demarrer.status_code == 403
    assert statut(chargement) == StatutChargement.VERIFIEE_PREPARATION


@pytest.mark.parametrize("role", [R.PREPARATION, R.COMMERCIAL, R.DIRECTION])
def test_ecran_ferme_aux_autres_roles(client, entreprise, tournee, role):
    client.force_login(UtilisateurFactory(entreprise=entreprise, roles=[role]))
    assert client.get(ECRAN).status_code == 403
    assert client.get(reverse("reception_tournee", args=[tournee.pk])).status_code == 403


def test_isolation_entre_entreprises(client, tournee):
    with activer_entreprise(None):
        etranger = UtilisateurFactory(entreprise=EntrepriseFactory(), roles=[R.CHAUFFEUR])
    client.force_login(etranger)
    assert client.get(reverse("reception_tournee", args=[tournee.pk])).status_code == 404


# --- Réception ------------------------------------------------------------------------------------


def test_confirmer_la_reception_puis_demarrer(chauffeur, tournee, personnes):
    chargement = verifier(affecter(tournee, personnes), personnes)
    reponse = recevoir(chauffeur, chargement, ["60", "20"], action="confirmer")
    assert reponse.url == reverse("reception_tournee", args=[tournee.pk])
    assert statut(chargement) == StatutChargement.RECUE_CHAUFFEUR
    texte = chauffeur.get(ECRAN).content.decode()
    assert "Démarrer la tournée" in texte and 'type="submit" disabled' not in texte
    chauffeur.post(reverse("reception_demarrer", args=[tournee.pk]))
    assert statut(tournee) == StatutTournee.EN_COURS
    assert statut(chargement.commande) == Statut.EN_LIVRAISON
    assert "Tournée partie à" in chauffeur.get(ECRAN).content.decode()


def test_demarrer_desactive_tant_que_tout_n_est_pas_recu(chauffeur, tournee, personnes):
    verifier(affecter(tournee, personnes), personnes)
    affecter(tournee, personnes)
    texte = chauffeur.get(ECRAN).content.decode()
    assert "2 commandes restantes" in texte and 'type="submit" disabled' in texte
    reponse = chauffeur.post(reverse("reception_demarrer", args=[tournee.pk]), follow=True)
    assert "Impossible depuis le statut" in reponse.content.decode()
    assert statut(tournee) == StatutTournee.EN_CHARGEMENT


def test_quantite_differente_refusee(chauffeur, tournee, personnes):
    chargement = verifier(affecter(tournee, personnes), personnes)
    reponse = recevoir(chauffeur, chargement, ["55", "20"], action="confirmer")
    assert reponse.status_code == 302
    texte = chauffeur.get(ECRAN).content.decode()
    assert "signaler un écart" in texte
    assert statut(chargement) == StatutChargement.VERIFIEE_PREPARATION


def test_signaler_un_ecart(chauffeur, tournee, personnes):
    chargement = verifier(affecter(tournee, personnes), personnes)
    recevoir(chauffeur, chargement, ["50", "20"], action="ecart", motif="10 manquants")
    assert statut(chargement) == StatutChargement.ECART_CONSTATE
    demande = exceptions(chargement, "ecart", Validation.Decision.EN_ATTENTE).get()
    assert demande.contenu["origine"] == "livreur"
    assert "Écart : 10 manquants" in chauffeur.get(ECRAN).content.decode()


def test_rafraichissement_htmx(chauffeur, tournee, personnes):
    affecter(tournee, personnes)
    texte = chauffeur.get(ECRAN, headers={"HX-Request": "true"}).content.decode()
    assert "Attendues" in texte and "<header" not in texte
    assert "every 30s" in chauffeur.get(ECRAN).content.decode()


# --- Prestataires ----------------------------------------------------------------------------


def test_prestataire_avec_compte(client, entreprise, personnes):
    compte = UtilisateurFactory(entreprise=entreprise, roles=[R.PRESTATAIRE])
    prestataire = PrestataireFactory(entreprise=entreprise, compte=compte)
    tournee = TourneeFactory(
        entreprise=entreprise, date=DEMAIN, chauffeur=None, prestataire=prestataire
    )
    chargement = verifier(affecter(tournee, personnes), personnes)
    client.force_login(compte)
    assert tournee.numero in client.get(ECRAN).content.decode()
    recevoir(client, chargement, ["60", "20"], action="confirmer")
    assert statut(chargement) == StatutChargement.RECUE_CHAUFFEUR


def test_prestataire_sans_compte_saisi_par_le_gestionnaire(client, entreprise, personnes):
    prestataire = PrestataireFactory(entreprise=entreprise)
    tournee = TourneeFactory(
        entreprise=entreprise, date=DEMAIN, chauffeur=None, prestataire=prestataire
    )
    chargement = verifier(affecter(tournee, personnes), personnes)
    client.force_login(personnes.gestionnaire)
    url = reverse("reception_tournee", args=[tournee.pk])
    assert url in client.get(reverse("ecran_tournees"), {"date": DEMAIN}).content.decode()
    texte = client.get(url).content.decode()
    assert f"saisie par {personnes.gestionnaire}" in texte
    reponse = recevoir(client, chargement, ["60", "20"], action="confirmer")
    assert reponse.url == url
    assert statut(chargement) == StatutChargement.VERIFIEE_PREPARATION
    assert "en attente de validation" in client.get(url).content.decode()
    assert exceptions(chargement, "reception", Validation.Decision.EN_ATTENTE).exists()
    # La Direction ne saisit pas à la place du prestataire (décision 8.3).
    client.force_login(personnes.direction)
    assert client.get(url).status_code == 403
