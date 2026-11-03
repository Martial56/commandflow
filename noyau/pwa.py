"""Application installable (PWA, décision 8.4) : manifeste, service worker et page de repli.

Pas de travail hors connexion au MVP (phase 2, §17 et §21) : le service worker, écrit à la
main (pas de Workbox, qui demande Node), garde en cache les fichiers statiques et la page
« hors connexion ». Les pages et les envois ne sont jamais mis en cache.
"""

import hashlib
import json

from django.contrib.staticfiles import finders
from django.http import JsonResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.urls import reverse
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET

# Fichiers gardés en cache pour les écrans de terrain.
FICHIERS_STATIQUES = [
    "css/comandflow.css",
    "vendor/htmx-2.0.4.min.js",
    "vendor/alpinejs-3.14.9.min.js",
    "vendor/signature_pad-5.0.4.min.js",
    "js/terrain.js",
    "icones/comandflow-192.png",
    "icones/comandflow-512.png",
]
ICONES = [
    {"src": "icones/comandflow-192.png", "sizes": "192x192", "purpose": "any"},
    {"src": "icones/comandflow-512.png", "sizes": "512x512", "purpose": "any"},
    {"src": "icones/comandflow-512.png", "sizes": "512x512", "purpose": "maskable"},
]


def version_statiques():
    """Empreinte du contenu des fichiers en cache : un nouveau déploiement renouvelle le
    cache du téléphone."""
    empreinte = hashlib.sha256()
    for chemin in FICHIERS_STATIQUES:
        fichier = finders.find(chemin)
        if fichier:
            with open(fichier, "rb") as contenu:
                empreinte.update(contenu.read())
    return empreinte.hexdigest()[:12]


@require_GET
def manifeste(request):
    return JsonResponse(
        {
            "name": "ComandFlow",
            "short_name": "ComandFlow",
            "description": "Commandes, production, tournées et livraisons",
            "lang": "fr",
            "start_url": "/",
            "scope": "/",
            "display": "standalone",
            "background_color": "#f6f4ef",
            "theme_color": "#1c1b19",
            "icons": [
                {**icone, "src": static(icone["src"]), "type": "image/png"} for icone in ICONES
            ],
        },
        content_type="application/manifest+json",
        json_dumps_params={"ensure_ascii": False},
    )


@require_GET
@cache_control(no_cache=True)
def service_worker(request):
    """Servi à la racine du site : sa portée couvre toute l'application."""
    return render(
        request,
        "pwa/sw.js",
        {
            "version": version_statiques(),
            # Chemins produits ici (static, reverse) : JSON écrit tel quel dans le script.
            "fichiers": json.dumps([static(chemin) for chemin in FICHIERS_STATIQUES]),
            "repli": json.dumps(reverse("noyau:hors_connexion")),
        },
        content_type="application/javascript",
    )


@require_GET
def hors_connexion(request):
    return render(request, "pwa/hors_connexion.html")
