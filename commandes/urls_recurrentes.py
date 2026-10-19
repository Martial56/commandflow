from django.urls import path

from . import views

urlpatterns = [
    # Nom `ecran_recurrentes` : entrée du menu (comptes/navigation.py).
    path("", views.recurrentes, name="ecran_recurrentes"),
    path("action/", views.recurrentes_action, name="recurrentes_action"),
]
