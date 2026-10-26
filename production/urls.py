from django.urls import path

from . import views

urlpatterns = [
    # Nom `ecran_production` : entrée du menu (comptes/navigation.py).
    path("", views.plan, name="ecran_production"),
    path("besoins/<int:pk>/a-produire/", views.ajuster, name="production_ajuster"),
    path("plans/<int:pk>/valider/", views.valider, name="production_valider"),
    path("plans/<int:pk>/cloturer/", views.cloturer, name="production_cloturer"),
    path("plans/<int:pk>/pretes/", views.marquer_pretes, name="production_pretes"),
    path("plans/<int:pk>/fiche-atelier/", views.fiche_atelier, name="production_fiche_atelier"),
]
