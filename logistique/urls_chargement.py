from django.urls import path

from . import views_chargement as views

urlpatterns = [
    # Nom `ecran_chargement` : écran d'arrivée de la Préparation (comptes/navigation.py).
    path("", views.liste, name="ecran_chargement"),
    path("<int:pk>/", views.controle, name="chargement_controle"),
    path("lignes/<int:pk>/", views.compter, name="chargement_compter"),
    path("<int:pk>/valider/", views.valider, name="chargement_valider"),
    path("<int:pk>/signaler/", views.signaler, name="chargement_signaler"),
    path("<int:pk>/completer/", views.completer, name="chargement_completer"),
]
