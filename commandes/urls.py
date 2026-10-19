from django.urls import path

from . import views

urlpatterns = [
    path("nouvelle/", views.nouvelle, name="commande_nouvelle"),
    path("nouvelle/adresses/", views.adresses, name="commande_adresses"),
    path("nouvelle/ligne/", views.ligne, name="commande_ligne"),
    path("nouvelle/calcul/", views.calcul, name="commande_calcul"),
    path("<int:pk>/", views.detail, name="commande_detail"),
]
