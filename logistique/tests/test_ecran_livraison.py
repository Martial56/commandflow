"""Écran « Preuve de livraison » (maquette 6) : résultat, quantités livrées, preuve,
accord client, motif ; §22-19, §22-20, §22-21 et §22-25 par l'écran."""

import base64
import html
import re
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from commandes.models import Statut
from comptes.models import Role
from entreprises.tests.factories import EntrepriseFactory
from logistique.models import (
    AccordClient,
    Livraison,
    MotifEchec,
    MoyenAccord,
    Preuve,
    Reliquat,
    StatutLivraison,
    TypePreuve,
)
from logistique.tests.factories import PrestataireFactory, TourneeFactory
from logistique.tests.test_depart import demarrer, recue
from logistique.tests.test_services_controle import statut
from logistique.tests.test_services_tournee import DEMAIN
from logistique.tests.test_transitions import utilisateur
from noyau.contexte import activer_entreprise
from noyau.models import Validation

pytestmark = pytest.mark.django_db
# PNG minimal (1 × 1), comme celui que produit signature_pad.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
SIGNATURE = "data:image/png;base64," + base64.b64encode(PNG).decode()


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
def livraison(entreprise, personnes):
    tournee = TourneeFactory(entreprise=entreprise, date=DEMAIN)
    chargements = [recue(tournee, personnes), recue(tournee, personnes)]
    demarrer(tournee)
    return Livraison.objects.get(chargement=chargements[0])


@pytest.fixture
def chauffeur(client, livraison):
    client.force_login(livraison.tournee.chauffeur)
    return client


def page(livraison):
    return reverse("livraison_saisie", args=[livraison.pk])


def texte(reponse):
    contenu = html.unescape(reponse.content.decode())
    return " ".join(re.sub(r"<[^>]+>", " ", contenu).split())


def envoyer(client, livraison, resultat, livres=(60, 20), **donnees):
    lignes = livraison.lignes.order_by("pk")
    valeurs = {f"livre-{ligne.pk}": q for ligne, q in zip(lignes, livres, strict=True)}
    return client.post(page(livraison), {"resultat": resultat, **valeurs, **donnees})


def test_formulaire_de_la_maquette(chauffeur, livraison):
    contenu = texte(chauffeur.get(page(livraison)))
    assert f"Arrêt 1 sur 2 · {livraison.commande.numero}" in contenu
    assert livraison.commande.client.nom in contenu and "Ouvrir l'itinéraire" in contenu
    for libelle in ("Livrée", "Partielle", "Refusée", "Client absent"):
        assert libelle in contenu
    assert "Produit Prévu Livré" in contenu
    assert "Exigée : photo du bordereau ou nom du réceptionnaire." in contenu
    assert "Photographier le bordereau signé" in contenu and "Signature" in contenu
    assert "Code client" not in contenu  # désactivé au Sprint 5 (8.4)
    assert "Heure et position enregistrées automatiquement à la validation." in contenu
    html_brut = chauffeur.get(page(livraison)).content.decode()
    premiere = livraison.lignes.order_by("pk").first()
    assert f'name="livre-{premiere.pk}" value="60"' in html_brut
    assert 'capture="environment"' in html_brut


def test_livree_avec_le_receptionnaire(chauffeur, livraison):
    """§22-19 : quantités réelles et preuve, depuis l'écran."""
    reponse = envoyer(
        chauffeur,
        livraison,
        "livree",
        receptionnaire_nom="Mme Touré",
        numero_bordereau="BL-1284",
        latitude="5.3364",
        longitude="-4.0267",
    )
    assert reponse.url == reverse("reception_tournee", args=[livraison.tournee.pk])
    livraison.refresh_from_db()
    assert livraison.statut == StatutLivraison.LIVREE
    assert (livraison.receptionnaire_nom, livraison.numero_bordereau) == ("Mme Touré", "BL-1284")
    assert livraison.latitude == Decimal("5.336400")
    assert statut(livraison.commande) == Statut.LIVREE
    assert "Livraison enregistrée." in texte(chauffeur.get(reverse("ecran_ma_tournee")))


def test_livree_avec_photo_et_signature(chauffeur, livraison):
    photo = SimpleUploadedFile("bordereau.jpg", b"\xff\xd8\xff", content_type="image/jpeg")
    envoyer(chauffeur, livraison, "livree", photo=photo, signature=SIGNATURE)
    assert statut(livraison) == StatutLivraison.LIVREE
    preuves = {preuve.type: preuve for preuve in Preuve.objects.filter(livraison=livraison)}
    assert set(preuves) == {TypePreuve.PHOTO_BORDEREAU, TypePreuve.SIGNATURE}
    assert preuves[TypePreuve.SIGNATURE].piece.fichier.read() == PNG


def test_livree_sans_preuve_reste_sur_le_formulaire(chauffeur, livraison):
    reponse = envoyer(chauffeur, livraison, "livree", commentaire="Rien")
    assert reponse.status_code == 400
    contenu = texte(reponse)
    assert "Preuve manquante : photo du bordereau ou nom du réceptionnaire." in contenu
    assert "Rien" in contenu  # la saisie est conservée
    assert statut(livraison) == StatutLivraison.EN_ATTENTE


def test_partielle_avec_accord(chauffeur, livraison):
    """§22-20 : accord client tracé, reliquat conservé."""
    envoyer(
        chauffeur,
        livraison,
        "partielle",
        livres=(40, 20),
        motif_partielle=MotifEchec.MARCHANDISE_MANQUANTE,
        accord_nom="M. Kouassi",
        accord_moyen=MoyenAccord.ORAL,
        receptionnaire_nom="M. Kouassi",
    )
    assert statut(livraison) == StatutLivraison.PARTIELLEMENT_LIVREE
    assert AccordClient.objects.get(livraison=livraison).nom_client == "M. Kouassi"
    assert Reliquat.objects.get().quantite == 20
    assert statut(livraison.commande) == Statut.PARTIELLEMENT_LIVREE


def test_partielle_sans_accord_refusee(chauffeur, livraison):
    reponse = envoyer(
        chauffeur,
        livraison,
        "partielle",
        livres=(40, 20),
        motif_partielle=MotifEchec.MARCHANDISE_MANQUANTE,
        receptionnaire_nom="M. Kouassi",
    )
    assert reponse.status_code == 400
    assert "accord du client" in texte(reponse)
    assert not Reliquat.objects.exists()


@pytest.mark.parametrize(
    ("resultat", "donnees", "motif"),
    [
        ("refusee", {"motif_refus": MotifEchec.ADRESSE_INCORRECTE}, MotifEchec.ADRESSE_INCORRECTE),
        ("client_absent", {}, MotifEchec.CLIENT_ABSENT),
    ],
)
def test_echec_avec_motif(chauffeur, livraison, resultat, donnees, motif):
    """§22-21 : un échec impose un motif."""
    reponse = envoyer(chauffeur, livraison, resultat, **donnees)
    assert reponse.status_code == 302
    livraison.refresh_from_db()
    assert (livraison.statut, livraison.motif_echec) == (StatutLivraison.ECHOUEE, motif)
    assert statut(livraison.commande) == Statut.EN_ANOMALIE


@pytest.mark.parametrize(
    ("donnees", "message"),
    [
        ({"resultat": ""}, "Choisir le résultat"),
        ({"resultat": "livree", "signature": "data:image/png;base64,pas-du-png"}, "Signature"),
        (
            {
                "resultat": "livree",
                "photo": SimpleUploadedFile("notes.txt", b"texte", content_type="text/plain"),
            },
            "doit être une image",
        ),
    ],
)
def test_saisie_illisible(chauffeur, livraison, donnees, message):
    reponse = chauffeur.post(page(livraison), donnees)
    assert reponse.status_code == 400 and message in texte(reponse)
    assert statut(livraison) == StatutLivraison.EN_ATTENTE


def test_arret_deja_traite(chauffeur, livraison):
    envoyer(chauffeur, livraison, "client_absent")
    reponse = chauffeur.get(page(livraison))
    assert reponse.url == reverse("reception_tournee", args=[livraison.tournee.pk])


def test_un_autre_chauffeur_est_refuse(client, livraison):
    """§22-25."""
    client.force_login(utilisateur(livraison, Role.CHAUFFEUR))
    assert client.get(page(livraison)).status_code == 403
    assert envoyer(client, livraison, "client_absent").status_code == 403
    assert statut(livraison) == StatutLivraison.EN_ATTENTE


def test_prestataire_sans_compte_saisi_par_le_gestionnaire(
    client, entreprise, personnes, livraison
):
    tournee = livraison.tournee
    tournee.chauffeur, tournee.prestataire = None, PrestataireFactory(entreprise=entreprise)
    tournee.save()
    client.force_login(personnes.gestionnaire)
    assert "(saisie par" in texte(client.get(page(livraison)))
    envoyer(client, livraison, "livree", receptionnaire_nom="Mme Touré")
    assert statut(livraison) == StatutLivraison.EN_ATTENTE
    assert Validation.objects.filter(decision=Validation.Decision.EN_ATTENTE).exists()
    contenu = texte(client.get(reverse("reception_tournee", args=[tournee.pk])))
    assert "un autre responsable doit la valider" in contenu
    assert "Saisie à valider" in contenu
