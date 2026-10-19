from django.urls import path

from . import views

urlpatterns = [
    # Nom `ecran_clients` : entrée du menu (comptes/navigation.py).
    path("", views.liste, name="ecran_clients"),
    path("nouveau/", views.nouveau, name="client_nouveau"),
    path("<int:pk>/", views.detail, name="client_detail"),
    path("<int:pk>/modifier/", views.modifier, name="client_modifier"),
    path(
        "<int:pk>/desactiver/",
        views.changer_activite,
        {"actif": False},
        name="client_desactiver",
    ),
    path(
        "<int:pk>/reactiver/",
        views.changer_activite,
        {"actif": True},
        name="client_reactiver",
    ),
    path("<int:pk>/adresses/ajouter/", views.ajouter_adresse, name="client_adresse_ajouter"),
    path(
        "<int:pk>/adresses/<int:adresse_pk>/par-defaut/",
        views.action_adresse,
        {"action": "par_defaut"},
        name="client_adresse_par_defaut",
    ),
    path(
        "<int:pk>/adresses/<int:adresse_pk>/desactiver/",
        views.action_adresse,
        {"action": "desactiver"},
        name="client_adresse_desactiver",
    ),
    path("<int:pk>/contacts/ajouter/", views.ajouter_contact, name="client_contact_ajouter"),
]
