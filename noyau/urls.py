from django.urls import path

from .fichiers import telecharger_fichier

app_name = "noyau"

urlpatterns = [
    path("fichiers/<str:jeton>/", telecharger_fichier, name="fichier"),
]
