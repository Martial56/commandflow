"""Application installable (décision 8.4) : manifeste, service worker, page de repli."""

import json

import pytest
from django.conf import settings
from django.contrib.staticfiles import finders
from django.templatetags.static import static
from django.urls import reverse

from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from noyau import pwa

pytestmark = pytest.mark.django_db


def test_manifeste(client):
    reponse = client.get(reverse("noyau:manifeste"))
    assert reponse.status_code == 200
    assert reponse["Content-Type"] == "application/manifest+json"
    manifeste = json.loads(reponse.content)
    assert (manifeste["name"], manifeste["lang"], manifeste["display"]) == (
        "ComandFlow",
        "fr",
        "standalone",
    )
    assert manifeste["start_url"] == "/" and manifeste["scope"] == "/"
    tailles = {(icone["sizes"], icone["purpose"]) for icone in manifeste["icons"]}
    assert {("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")} <= tailles


def test_icones_presentes():
    for icone in pwa.ICONES:
        chemin = finders.find(icone["src"])
        assert chemin, icone["src"]
        with open(chemin, "rb") as fichier:
            assert fichier.read(8) == b"\x89PNG\r\n\x1a\n"


def test_service_worker(client):
    reponse = client.get("/sw.js")
    assert reponse.status_code == 200
    assert reponse["Content-Type"].startswith("application/javascript")
    assert "no-cache" in reponse["Cache-Control"]
    contenu = reponse.content.decode()
    assert f'const CACHE = "comandflow-{pwa.version_statiques()}";' in contenu
    for chemin in pwa.FICHIERS_STATIQUES:
        assert f'"{static(chemin)}"' in contenu
    assert f'const REPLI = "{reverse("noyau:hors_connexion")}";' in contenu
    assert f"const FICHIERS = {json.dumps([static(c) for c in pwa.FICHIERS_STATIQUES])};" in contenu
    # Les envois et les pages ne sont jamais mis en cache.
    assert 'if (requete.method !== "GET") return;' in contenu


def test_version_suit_le_contenu(monkeypatch, tmp_path):
    fichier = tmp_path / "terrain.js"
    fichier.write_text("version 1")
    monkeypatch.setattr(pwa, "FICHIERS_STATIQUES", ["js/terrain.js"])
    monkeypatch.setattr(pwa.finders, "find", lambda chemin: str(fichier))
    avant = pwa.version_statiques()
    assert pwa.version_statiques() == avant
    fichier.write_text("version 2")
    assert pwa.version_statiques() != avant


def test_page_hors_connexion_sans_donnee_personnelle(client, entreprise):
    utilisateur = UtilisateurFactory(entreprise=entreprise, roles=[Role.CHAUFFEUR])
    client.force_login(utilisateur)
    reponse = client.get(reverse("noyau:hors_connexion"))
    assert reponse.status_code == 200
    contenu = reponse.content.decode()
    assert "Pas de connexion" in contenu and "Rien n'a été envoyé" in contenu
    assert utilisateur.username not in contenu and entreprise.nom not in contenu
    assert "csrf" not in contenu.lower()


def test_accessibles_sans_connexion(client):
    for url in (reverse("noyau:manifeste"), "/sw.js", reverse("noyau:hors_connexion")):
        assert client.get(url).status_code == 200, url


def test_pages_liees_au_manifeste(client, entreprise, settings):
    settings.MIDDLEWARE = [
        m for m in settings.MIDDLEWARE if "DoubleAuthentificationObligatoire" not in m
    ]
    client.force_login(UtilisateurFactory(entreprise=entreprise, roles=[Role.CHAUFFEUR]))
    contenu = client.get(reverse("ecran_ma_tournee")).content.decode()
    assert f'<link rel="manifest" href="{reverse("noyau:manifeste")}">' in contenu
    assert static("js/terrain.js") in contenu  # enregistre le service worker


def test_polices_servies_par_l_application():
    """Les polices sont dans les fichiers statiques et en cache sur le téléphone : aucune
    page ne dépend de Google Fonts (chargement bloquant sur un réseau lent)."""
    source = (settings.BASE_DIR / "assets/css/source.css").read_text(encoding="utf-8")
    for chemin in (
        "vendor/fonts/ibm-plex-sans-latin.woff2",
        "vendor/fonts/ibm-plex-mono-500-latin.woff2",
    ):
        fichier = finders.find(chemin)
        assert fichier, chemin
        with open(fichier, "rb") as contenu:
            assert contenu.read(4) == b"wOF2"
        assert chemin in pwa.FICHIERS_STATIQUES
        # Chemin relatif à la feuille compilée static/css/comandflow.css.
        assert f'url("../{chemin}")' in source


def test_aucune_page_ne_charge_google_fonts(client):
    utilisateur = UtilisateurFactory(roles=[Role.GESTIONNAIRE])
    assert "fonts.googleapis.com" not in client.get(reverse("account_login")).text
    client.force_login(utilisateur)
    assert "fonts.googleapis.com" not in client.get(reverse("ecran_commandes")).text
