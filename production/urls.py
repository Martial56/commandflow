from django.urls import path

from . import views, views_saisie

urlpatterns = [
    # Nom `ecran_production` : entrée du menu (comptes/navigation.py).
    path("", views.plan, name="ecran_production"),
    path("besoins/<int:pk>/a-produire/", views.ajuster, name="production_ajuster"),
    path("plans/<int:pk>/valider/", views.valider, name="production_valider"),
    path("plans/<int:pk>/cloturer/", views.cloturer, name="production_cloturer"),
    path("plans/<int:pk>/pretes/", views.marquer_pretes, name="production_pretes"),
    path("plans/<int:pk>/fiche-atelier/", views.fiche_atelier, name="production_fiche_atelier"),
    # Saisie sur téléphone (maquette 2).
    path("saisie/", views_saisie.saisie, name="production_saisie"),
    path(
        "saisie/besoins/<int:pk>/", views_saisie.enregistrer, name="production_saisie_enregistrer"
    ),
    path(
        "saisie/saisies/<int:pk>/annuler/", views_saisie.annuler, name="production_saisie_annuler"
    ),
]
