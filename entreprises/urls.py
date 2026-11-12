from django.urls import path

from . import views

urlpatterns = [
    path("", views.ecran, name="ecran_parametres"),
    path("comptes/ajouter/", views.ajouter_compte, name="parametres_ajouter_compte"),
    path("comptes/<int:pk>/reinitialiser/", views.reinitialiser, name="parametres_reinitialiser"),
    path("comptes/<int:pk>/desactiver/", views.desactiver, name="parametres_desactiver"),
    path("comptes/<int:pk>/reactiver/", views.reactiver, name="parametres_reactiver"),
    path("regles/", views.regles, name="parametres_regles"),
    path("preuves/", views.preuves, name="parametres_preuves"),
]
