from django.urls import path

from . import exports, views

urlpatterns = [
    # Nom `ecran_paiements` : entrée du menu (comptes/navigation.py).
    path("", views.ecran, name="ecran_paiements"),
    path("affectations/", views.affectations, name="paiement_affectations"),
    path("enregistrer/", views.enregistrer, name="paiement_enregistrer"),
    path("clients/<int:pk>/", views.releve, name="releve_client"),
    path("clients/<int:pk>/releve.pdf", exports.releve_pdf, name="releve_client_pdf"),
    path("creances.xlsx", exports.creances_excel, name="creances_excel"),
    path("creances.pdf", exports.creances_pdf, name="creances_pdf"),
    path("paiements.xlsx", exports.paiements_excel, name="paiements_excel"),
    path("<int:pk>/affecter/", views.affecter, name="paiement_affecter"),
    path("<int:pk>/annuler/", views.annuler, name="paiement_annuler"),
]
