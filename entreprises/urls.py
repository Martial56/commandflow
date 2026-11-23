from django.urls import path

from . import views

urlpatterns = [
    path("", views.ecran, name="ecran_parametres"),
    path("comptes/ajouter/", views.ajouter_compte, name="parametres_ajouter_compte"),
    path("comptes/<int:pk>/reinitialiser/", views.reinitialiser, name="parametres_reinitialiser"),
    path("comptes/<int:pk>/desactiver/", views.desactiver, name="parametres_desactiver"),
    path("comptes/<int:pk>/reactiver/", views.reactiver, name="parametres_reactiver"),
    path("produits/ajouter/", views.ajouter_produit, name="parametres_ajouter_produit"),
    path("produits/<int:pk>/modifier/", views.modifier_produit, name="parametres_modifier_produit"),
    path(
        "produits/<int:pk>/desactiver/",
        views.desactiver_produit,
        name="parametres_desactiver_produit",
    ),
    path(
        "produits/<int:pk>/reactiver/", views.reactiver_produit, name="parametres_reactiver_produit"
    ),
    path("regles/", views.regles, name="parametres_regles"),
    path("preuves/", views.preuves, name="parametres_preuves"),
]
