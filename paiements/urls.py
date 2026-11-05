from django.urls import path

from . import views

urlpatterns = [
    # Nom `ecran_paiements` : entrée du menu (comptes/navigation.py).
    path("", views.ecran, name="ecran_paiements"),
    path("affectations/", views.affectations, name="paiement_affectations"),
    path("enregistrer/", views.enregistrer, name="paiement_enregistrer"),
    path("clients/<int:pk>/", views.releve, name="releve_client"),
    path("<int:pk>/affecter/", views.affecter, name="paiement_affecter"),
    path("<int:pk>/annuler/", views.annuler, name="paiement_annuler"),
]
