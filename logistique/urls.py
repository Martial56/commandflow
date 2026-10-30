from django.urls import path

from . import views

urlpatterns = [
    # Nom `ecran_tournees` : entrée du menu (comptes/navigation.py).
    path("", views.tableau, name="ecran_tournees"),
    path("creer/", views.creer, name="tournee_creer"),
    path("affecter/", views.affecter, name="tournee_affecter"),
    path("<int:pk>/livreur/", views.changer_livreur, name="tournee_livreur"),
    path("<int:pk>/ordre/", views.proposer_ordre, name="tournee_proposer_ordre"),
    path("chargements/<int:pk>/retirer/", views.retirer, name="chargement_retirer"),
    path("chargements/<int:pk>/deplacer/", views.deplacer, name="chargement_deplacer"),
    path(
        "chargements/<int:pk>/exception-depart/",
        views.exception_depart,
        name="chargement_exception_depart",
    ),
    path("demandes/<int:pk>/decider/", views.decider, name="chargement_decider"),
]
