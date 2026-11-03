"""Livraisons sur la fiche commande (maquette 10) : bloc « Livraisons » (quantités,
réceptionnaire, preuves par lien signé, accord, reliquats, retours), tournée affichée
après la livraison, historique reconstruit depuis pghistory."""

import datetime
import html
import re

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from logistique import services
from logistique.models import DecisionRetour, EtatRetour, Livraison, MotifEchec, MoyenAccord
from logistique.services import Resultat
from logistique.tests.factories import PrestataireFactory, TourneeFactory
from logistique.tests.test_depart import demarrer, recue
from logistique.tests.test_services_tournee import DEMAIN
from noyau.contexte import activer_entreprise, activer_utilisateur
from noyau.models import Validation

pytestmark = pytest.mark.django_db
APRES_DEMAIN = DEMAIN + datetime.timedelta(days=1)


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
    chargement = recue(tournee, personnes)
    demarrer(tournee)
    return Livraison.objects.get(chargement=chargement)


def cloturer(livraison, resultat, livres=(60, 20), par=None, **kwargs):
    with activer_utilisateur(par or livraison.tournee.chauffeur):
        if resultat != Resultat.ECHEC:
            kwargs["quantites"] = dict(zip(livraison.lignes.order_by("pk"), livres, strict=True))
            kwargs.setdefault("receptionnaire_nom", "Mme Bamba")
        return services.cloturer_livraison(livraison, resultat, **kwargs)


def partielle(livraison):
    return cloturer(
        livraison,
        Resultat.PARTIELLE,
        (40, 20),
        motif=MotifEchec.MARCHANDISE_MANQUANTE,
        accord={"nom_client": "M. Kouassi", "moyen": MoyenAccord.ORAL},
    )


def fiche(client, personne, livraison):
    client.force_login(personne)
    reponse = client.get(reverse("commande_detail", args=[livraison.commande_id]))
    return reponse.content.decode()


def texte(contenu):
    return " ".join(re.sub(r"<[^>]+>", " ", html.unescape(contenu)).split())


def produit(livraison, rang=0):
    return livraison.lignes.order_by("pk")[rang].ligne_commande.produit


def test_livraison_effectuee(client, personnes, livraison):
    photo = SimpleUploadedFile("bordereau.jpg", b"\xff\xd8\xff", content_type="image/jpeg")
    with activer_utilisateur(livraison.tournee.chauffeur):
        services.arriver_chez_client(livraison)
    cloturer(
        livraison,
        Resultat.LIVREE,
        numero_bordereau="BL-1284",
        photo=photo,
        latitude="5.3364",
        longitude="-4.0267",
    )
    contenu = fiche(client, personnes.gestionnaire, livraison)
    page = texte(contenu)
    tournee = livraison.tournee
    assert f"Livraison {tournee.numero} · " in page and "Livrée" in page
    assert "reçue par Mme Bamba · bordereau BL-1284" in page
    assert f"Produit Prévu Livré {produit(livraison).nom} 60 60" in page
    assert "Preuves : Photo du bordereau Position GPS" in page
    # La tournée reste affichée une fois livrée (et non « Non affectée »).
    assert f"{tournee.numero} · {tournee.chauffeur} · arrêt 1" in page
    for attendu in (
        "Livreur arrivé chez le client",
        "Livraison effectuée (reçue par Mme Bamba)",
        "Commande livrée",
    ):
        assert attendu in page, attendu


def test_lien_signe_de_la_preuve(client, personnes, livraison):
    photo = SimpleUploadedFile("bordereau.jpg", b"\xff\xd8\xff photo", content_type="image/jpeg")
    cloturer(livraison, Resultat.LIVREE, photo=photo)
    contenu = fiche(client, personnes.gestionnaire, livraison)
    lien = re.search(r'href="([^"]+)">Photo du bordereau', contenu).group(1)
    assert client.get(lien).status_code == 200
    with activer_entreprise(None):
        etranger = UtilisateurFactory(entreprise=EntrepriseFactory(), roles=[Role.GESTIONNAIRE])
    client.force_login(etranger)
    assert client.get(lien).status_code == 404  # lien personnel, limité à l'entreprise


def test_livraison_partielle_et_reliquat(client, personnes, livraison):
    partielle(livraison)
    with activer_utilisateur(personnes.gestionnaire):
        services.planifier_reliquat(livraison.commande, APRES_DEMAIN)
    page = texte(fiche(client, personnes.gestionnaire, livraison))
    nom = produit(livraison).nom
    assert "Accord du client : M. Kouassi (oral)" in page
    assert f"Reliquat : 20 {nom} · prévu le" in page
    for attendu in (
        "Livraison partielle (marchandise manquante), avec l'accord du client",
        f"Reliquat noté : 20 {nom}",
        "Partiellement livrée : un reliquat reste à livrer",
        "Reliquat planifié pour le",
    ):
        assert attendu in page, attendu


def test_reliquat_solde(client, personnes, livraison):
    partielle(livraison)
    with activer_utilisateur(personnes.gestionnaire):
        demande = services.demander_solde_reliquat(livraison.commande, "Client fermé")
    with activer_utilisateur(personnes.direction):
        services.decider_solde_reliquat(demande, accepter=True)
    page = texte(fiche(client, personnes.gestionnaire, livraison))
    for attendu in (
        "Demande de validation pour le solde du reliquat : « Client fermé »",
        "Exception pour le solde du reliquat acceptée",
        f"Reliquat soldé : 20 {produit(livraison).nom}",
        "Commande livrée : reliquat livré ou soldé",
        "· soldé",
    ):
        assert attendu in page, attendu


def test_echec_puis_report(client, personnes, livraison):
    cloturer(livraison, Resultat.ECHEC, motif=MotifEchec.CLIENT_ABSENT, commentaire="Fermé")
    livraison.refresh_from_db()
    with activer_utilisateur(personnes.gestionnaire):
        services.reporter_livraison(livraison, APRES_DEMAIN)
    page = texte(fiche(client, personnes.gestionnaire, livraison))
    assert "Motif : client absent · reportée au" in page and "« Fermé »" in page
    assert "Produit Prévu Livré" not in page  # rien de livré
    for attendu in (
        "Livraison non effectuée : client absent",
        "En anomalie : livraison non effectuée",
        "Livraison reportée au",
    ):
        assert attendu in page, attendu
    # Relancée : la commande n'est plus bloquée par la tournée passée.
    assert "Demander une modification" in page


def test_retour_decide(client, personnes, livraison):
    cloturer(livraison, Resultat.LIVREE)
    with activer_utilisateur(livraison.tournee.chauffeur):
        retour = services.declarer_retour(
            livraison,
            produit=produit(livraison),
            quantite=5,
            motif="Écrasées",
            etat=EtatRetour.ABIME,
        )
    with activer_utilisateur(personnes.direction):
        services.decider_retour(retour, DecisionRetour.DESTRUCTION)
    page = texte(fiche(client, personnes.gestionnaire, livraison))
    nom = produit(livraison).nom
    assert (
        f"Retour : 5 {nom} · abîmé, à détruire · Écrasées · destruction ({personnes.direction})"
        in page
    )
    assert f"Retour déclaré : 5 {nom} (abîmé, à détruire, « Écrasées »)" in page
    assert f"Retour 5 {nom} : destruction" in page


def test_saisie_du_prestataire_dans_l_historique(client, entreprise, personnes, livraison):
    tournee = livraison.tournee
    tournee.chauffeur, tournee.prestataire = None, PrestataireFactory(entreprise=entreprise)
    tournee.save()
    cloturer(livraison, Resultat.LIVREE, par=personnes.gestionnaire)
    demande = Validation.objects.get(decision=Validation.Decision.EN_ATTENTE)
    with activer_utilisateur(personnes.direction):
        services.decider_livraison_prestataire(demande, accepter=True)
    page = texte(fiche(client, personnes.gestionnaire, livraison))
    assert "Demande de validation pour la livraison saisie pour le prestataire" in page
    assert "Exception pour la livraison saisie pour le prestataire acceptée" in page
    assert "Livraison effectuée (reçue par Mme Bamba)" in page


def test_sans_livraison_pas_de_bloc(client, entreprise, personnes):
    tournee = TourneeFactory(entreprise=entreprise, date=DEMAIN)
    chargement = recue(tournee, personnes)
    client.force_login(personnes.gestionnaire)
    page = texte(
        client.get(reverse("commande_detail", args=[chargement.commande_id])).content.decode()
    )
    assert "Livraison " + tournee.numero not in page
