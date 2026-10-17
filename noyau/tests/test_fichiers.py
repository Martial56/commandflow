import time
from unittest import mock

import pytest
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile

from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from noyau.contexte import AutreEntreprise, activer_entreprise, activer_utilisateur
from noyau.fichiers import url_signee
from noyau.models import PieceJointe
from noyau.services import joindre_fichier
from noyau.tests.models import ObjetTest

pytestmark = pytest.mark.django_db

CONTENU = b"%PDF-1.4 bordereau de test"


@pytest.fixture(autouse=True)
def stockage_temporaire(monkeypatch, tmp_path):
    # Le stockage d'un FileField est résolu une fois au chargement : on le remplace
    # directement pour ne jamais écrire dans le vrai dossier privé.
    champ = PieceJointe._meta.get_field("fichier")
    monkeypatch.setattr(champ, "storage", FileSystemStorage(location=tmp_path))
    return tmp_path


@pytest.fixture
def piece_jointe(utilisateur):
    with activer_entreprise(utilisateur.entreprise), activer_utilisateur(utilisateur):
        objet = ObjetTest.objects.create(nom="livraison fictive")
        fichier = SimpleUploadedFile("Bordereau Client X.pdf", CONTENU, "application/pdf")
        return joindre_fichier(objet, fichier, PieceJointe.Type.JUSTIFICATIF)


def test_fichier_stocke_sous_un_nom_aleatoire(piece_jointe, stockage_temporaire):
    assert piece_jointe.nom_original == "Bordereau Client X.pdf"
    assert "Bordereau" not in piece_jointe.fichier.name
    assert piece_jointe.fichier.name.startswith(f"{piece_jointe.entreprise_id}/")
    assert (stockage_temporaire / piece_jointe.fichier.name).read_bytes() == CONTENU


def test_jointure_refusee_sur_un_objet_d_une_autre_entreprise(utilisateur):
    autre = ObjetTest.toutes_entreprises.create(entreprise=EntrepriseFactory(), nom="autre")
    fichier = SimpleUploadedFile("x.pdf", CONTENU)
    with activer_entreprise(utilisateur.entreprise), pytest.raises(AutreEntreprise):
        joindre_fichier(autre, fichier, PieceJointe.Type.PHOTO)


def test_acces_autorise(client, utilisateur, piece_jointe):
    client.force_login(utilisateur)
    reponse = client.get(url_signee(piece_jointe, utilisateur))
    assert reponse.status_code == 200
    assert b"".join(reponse.streaming_content) == CONTENU
    assert "no-store" in reponse["Cache-Control"]
    assert "Bordereau Client X.pdf" in reponse["Content-Disposition"]


def test_visiteur_anonyme_redirige_vers_la_connexion(client, utilisateur, piece_jointe):
    reponse = client.get(url_signee(piece_jointe, utilisateur))
    assert reponse.status_code == 302


def test_acces_refuse_a_une_autre_entreprise(client, piece_jointe):
    intrus = UtilisateurFactory(entreprise=EntrepriseFactory())
    client.force_login(intrus)
    # Même avec un lien signé à son nom, la pièce d'une autre entreprise reste introuvable.
    assert client.get(url_signee(piece_jointe, intrus)).status_code == 404


def test_lien_personnel_non_transmissible(client, utilisateur, piece_jointe):
    collegue = UtilisateurFactory(entreprise=utilisateur.entreprise)
    client.force_login(collegue)
    assert client.get(url_signee(piece_jointe, utilisateur)).status_code == 404


def test_lien_expire(client, settings, utilisateur, piece_jointe):
    url = url_signee(piece_jointe, utilisateur)
    client.force_login(utilisateur)
    plus_tard = time.time() + settings.DUREE_LIEN_FICHIER + 1
    with mock.patch("django.core.signing.time.time", return_value=plus_tard):
        assert client.get(url).status_code == 404


def test_lien_falsifie(client, utilisateur, piece_jointe):
    url = url_signee(piece_jointe, utilisateur)
    client.force_login(utilisateur)
    assert client.get(url.replace("/fichiers/", "/fichiers/x")).status_code == 404
