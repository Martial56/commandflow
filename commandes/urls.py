from django.urls import path

from . import views

urlpatterns = [
    # Nom `ecran_commandes` : entrée du menu (comptes/navigation.py).
    path("", views.liste, name="ecran_commandes"),
    path("nouvelle/", views.nouvelle, name="commande_nouvelle"),
    path("nouvelle/adresses/", views.adresses, name="commande_adresses"),
    path("nouvelle/ligne/", views.ligne, name="commande_ligne"),
    path("nouvelle/calcul/", views.calcul, name="commande_calcul"),
    path("<int:pk>/", views.detail, name="commande_detail"),
    path("<int:pk>/modifier/", views.modifier, name="commande_modifier"),
    path("<int:pk>/soumettre/", views.soumettre, name="commande_soumettre"),
    path("<int:pk>/confirmer/", views.confirmer, name="commande_confirmer"),
    path("<int:pk>/annuler/", views.annuler, name="commande_annuler"),
    path(
        "<int:pk>/demander-modification/",
        views.demander_modification,
        name="commande_demander_modification",
    ),
    path("<int:pk>/prete/", views.marquer_prete, name="commande_marquer_prete"),
    path(
        "<int:pk>/demander-exception/",
        views.demander_exception,
        name="commande_demander_exception",
    ),
    path(
        "<int:pk>/demander-annulation/",
        views.demander_annulation,
        name="commande_demander_annulation",
    ),
    path(
        "<int:pk>/demandes/<int:validation_pk>/decider/",
        views.decider,
        name="commande_decider",
    ),
]
