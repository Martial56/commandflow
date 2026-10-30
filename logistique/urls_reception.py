from django.urls import path

from . import views_reception as views

urlpatterns = [
    # Nom `ecran_ma_tournee` : écran d'arrivée du Chauffeur et du Prestataire.
    path("", views.ma_tournee, name="ecran_ma_tournee"),
    path("<int:pk>/", views.reception, name="reception_tournee"),
    path("<int:pk>/demarrer/", views.demarrer, name="reception_demarrer"),
    path("chargements/<int:pk>/recevoir/", views.recevoir, name="reception_recevoir"),
]
