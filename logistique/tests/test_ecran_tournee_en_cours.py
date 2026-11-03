"""« Ma tournée » une fois partie (maquette 5) : progression, prochain arrêt, arrivée,
arrêts traités, incident ; §22-25."""

import datetime
import html
import re
from decimal import Decimal

import pytest
from django.urls import reverse

from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from logistique import services
from logistique.models import Incident, Livraison, MotifEchec, StatutLivraison, TypeIncident
from logistique.services import Resultat
from logistique.tests.factories import TourneeFactory
from logistique.tests.test_depart import demarrer, recue
from logistique.tests.test_services_controle import statut
from logistique.tests.test_services_tournee import DEMAIN
from logistique.tests.test_transitions import utilisateur
from noyau.contexte import activer_entreprise, activer_utilisateur

pytestmark = pytest.mark.django_db
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


def partie(entreprise, personnes, arrets=3):
    """Tournée partie avec `arrets` commandes (lignes prévues 60 et 20)."""
    tournee = TourneeFactory(entreprise=entreprise, date=DEMAIN)
    chargements = [recue(tournee, personnes) for _ in range(arrets)]
    demarrer(tournee)
    return tournee, [Livraison.objects.get(chargement=c) for c in chargements]


@pytest.fixture
def tournee_partie(entreprise, personnes):
    return partie(entreprise, personnes)


@pytest.fixture
def chauffeur(client, tournee_partie):
    client.force_login(tournee_partie[0].chauffeur)
    return client


def texte(reponse):
    contenu = html.unescape(reponse.content.decode())
    return " ".join(re.sub(r"<[^>]+>", " ", contenu).split())


def traiter(livraison, resultat=Resultat.LIVREE):
    with activer_utilisateur(livraison.tournee.chauffeur):
        if resultat == Resultat.ECHEC:
            services.cloturer_livraison(livraison, resultat, motif=MotifEchec.CLIENT_ABSENT)
        else:
            quantites = dict(zip(livraison.lignes.order_by("pk"), [60, 20], strict=True))
            services.cloturer_livraison(
                livraison, resultat, quantites=quantites, receptionnaire_nom="Mme Bamba"
            )


def test_prochain_arret_et_progression(chauffeur, tournee_partie):
    tournee, (premiere, seconde, troisieme) = tournee_partie
    page = texte(chauffeur.get(ECRAN))
    assert f"{tournee.numero} · en cours depuis" in page
    assert "0 arrêts traités sur 3" in page
    client = premiere.commande.client.nom
    assert f"Arrêt 1 · prochain 06h–08h {client}" in page
    assert "2 produits · 80 pièces · preuve : photo du bordereau ou nom du réceptionnaire" in page
    assert "Je suis arrivé" in page and "Ouvrir l'itinéraire" in page
    for rang, livraison in ((2, seconde), (3, troisieme)):
        assert f"Arrêt {rang} {livraison.commande.client.nom} À venir" in page
    assert "Déjà traités" not in page
    assert "Signaler un incident (panne, accident…)" in page


def test_itineraire_vers_les_coordonnees_ou_l_adresse(chauffeur, tournee_partie):
    premiere = tournee_partie[1][0]
    contenu = chauffeur.get(ECRAN).content.decode()
    assert "https://www.google.com/maps/dir/?api=1&amp;destination=Rue%20des%20Jardins" in contenu
    adresse = premiere.commande.adresse
    adresse.latitude, adresse.longitude = Decimal("5.336400"), Decimal("-4.026700")
    adresse.save()
    contenu = chauffeur.get(ECRAN).content.decode()
    assert "destination=5.336400%2C-4.026700" in contenu


def test_je_suis_arrive(chauffeur, tournee_partie):
    tournee, (premiere, *_) = tournee_partie
    reponse = chauffeur.post(
        reverse("livraison_arriver", args=[premiere.pk]),
        {"latitude": "5.3364", "longitude": "-4.0267"},
    )
    assert reponse.url == reverse("livraison_saisie", args=[premiere.pk])
    premiere.refresh_from_db()
    assert premiere.statut == StatutLivraison.SUR_PLACE
    assert premiere.latitude == Decimal("5.336400")
    page = texte(chauffeur.get(ECRAN))
    assert "Arrêt 1 · sur place depuis" in page and "Je suis arrivé" not in page


def test_arrivee_sans_position(chauffeur, tournee_partie):
    premiere = tournee_partie[1][0]
    chauffeur.post(reverse("livraison_arriver", args=[premiere.pk]), {"latitude": ""})
    assert statut(premiere) == StatutLivraison.SUR_PLACE


def test_arrets_deja_traites(chauffeur, tournee_partie):
    _, (premiere, seconde, troisieme) = tournee_partie
    traiter(premiere)
    traiter(seconde, Resultat.ECHEC)
    page = texte(chauffeur.get(ECRAN))
    assert "2 arrêts traités sur 3" in page
    assert f"Arrêt 3 · prochain 06h–08h {troisieme.commande.client.nom}" in page
    # Du plus récent au plus ancien, chacun avec sa pastille.
    traites = page.split("Déjà traités", 1)[1]
    echec = traites.index(f"2 · {seconde.commande.client.nom} Échec")
    assert echec < traites.index(f"1 · {premiere.commande.client.nom} Livrée")


def test_tournee_terminee_du_jour(client, entreprise, personnes):
    tournee, livraisons = partie(entreprise, personnes, arrets=1)
    # Tournée du jour (préparation du test, sans service).
    type(tournee).toutes_entreprises.filter(pk=tournee.pk).update(date=datetime.date.today())
    traiter(livraisons[0])
    client.force_login(tournee.chauffeur)
    page = texte(client.get(ECRAN))
    assert f"{tournee.numero} · terminée à" in page
    assert "Tournée terminée · 1 arrêt traité sur 1" in page
    assert "Signaler un incident" not in page


def test_rafraichissement_renvoie_le_fragment(chauffeur, tournee_partie):
    tournee = tournee_partie[0]
    contenu = chauffeur.get(
        reverse("reception_tournee", args=[tournee.pk]), HTTP_HX_REQUEST="true"
    ).content.decode()
    assert "<header" not in contenu
    assert 'id="progression" class="flex flex-col gap-2.5 pt-1" hx-swap-oob="true"' in contenu


def test_signaler_un_incident(chauffeur, tournee_partie):
    tournee = tournee_partie[0]
    chauffeur.post(
        reverse("tournee_incident", args=[tournee.pk]),
        {"type": TypeIncident.PANNE, "commentaire": "Pneu crevé à Marcory"},
    )
    incident = Incident.objects.get()
    assert (incident.tournee, incident.type) == (tournee, TypeIncident.PANNE)
    assert "Incident signalé au gestionnaire." in texte(chauffeur.get(ECRAN))


def test_un_autre_chauffeur_ne_voit_rien(client, tournee_partie):
    """§22-25 : un chauffeur ne voit pas la tournée d'un autre."""
    tournee, (premiere, *_) = tournee_partie
    autre = utilisateur(tournee, Role.CHAUFFEUR)
    client.force_login(autre)
    assert tournee.numero not in texte(client.get(ECRAN))
    assert client.get(reverse("reception_tournee", args=[tournee.pk])).status_code == 403
    reponse = client.post(reverse("livraison_arriver", args=[premiere.pk]))
    assert reponse.status_code == 403
    reponse = client.post(reverse("tournee_incident", args=[tournee.pk]), {"type": "panne"})
    assert reponse.status_code == 403
    assert statut(premiere) == StatutLivraison.EN_ATTENTE
    assert not Incident.objects.exists()


def test_isolation_entre_entreprises(client, tournee_partie):
    premiere = tournee_partie[1][0]
    with activer_entreprise(None):
        etranger = UtilisateurFactory(entreprise=EntrepriseFactory(), roles=[Role.CHAUFFEUR])
    client.force_login(etranger)
    assert client.post(reverse("livraison_arriver", args=[premiere.pk])).status_code == 404
