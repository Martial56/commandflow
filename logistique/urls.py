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
    path("<int:pk>/retours/", views.declarer_retour, name="tournee_retour"),
    path("retours/<int:pk>/decider/", views.decider_retour, name="retour_decider"),
    path("livraisons/<int:pk>/reporter/", views.reporter, name="livraison_reporter"),
    path("livraisons/<int:pk>/abandon/", views.demander_abandon, name="livraison_abandon"),
    path("saisies/<int:pk>/decider/", views.decider_livraison, name="livraison_decider"),
    path("reliquats/<int:pk>/planifier/", views.planifier_reliquat, name="reliquat_planifier"),
    path("reliquats/<int:pk>/solder/", views.demander_solde, name="reliquat_solder"),
    path("soldes/<int:pk>/decider/", views.decider_solde, name="solde_decider"),
]
