from django.urls import path

from . import pwa
from .fichiers import telecharger_fichier

app_name = "noyau"

urlpatterns = [
    path("fichiers/<str:jeton>/", telecharger_fichier, name="fichier"),
    # Application installable (décision 8.4) : à la racine, pour couvrir tout le site.
    path("manifest.webmanifest", pwa.manifeste, name="manifeste"),
    path("sw.js", pwa.service_worker, name="service_worker"),
    path("hors-connexion/", pwa.hors_connexion, name="hors_connexion"),
]
