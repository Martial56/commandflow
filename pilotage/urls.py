from django.urls import path

from . import views

urlpatterns = [
    # Nom `ecran_tableau_de_bord` : entrée du menu et écran d'arrivée (comptes/navigation.py).
    path("", views.tableau, name="ecran_tableau_de_bord"),
    path("export.xlsx", views.export, name="tableau_de_bord_export"),
]
