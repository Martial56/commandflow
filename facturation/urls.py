from django.urls import path

from . import views

urlpatterns = [
    # Nom `ecran_facturation` : entrée du menu (comptes/navigation.py).
    path("", views.ecran, name="ecran_facturation"),
    path("emettre/", views.emettre, name="facture_emettre"),
    path("<int:pk>/", views.document, name="facture_detail"),
    path("<int:pk>/joindre/", views.joindre, name="facture_joindre"),
    path("<int:pk>/annuler/", views.annuler, name="facture_annuler"),
    path("<int:pk>/verifier/", views.verifier, name="facture_verifier"),
]
