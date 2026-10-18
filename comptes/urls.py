from django.urls import path

from . import views
from .navigation import ECRANS

urlpatterns = [path("", views.accueil, name="accueil")]

# Écrans en attente de leur sprint : chaque sprint remplace la ligne de son écran
# par les URL de son app (en gardant le nom `ecran_<code>` pour la navigation).
urlpatterns += [
    path(ecran.chemin, views.ecran_a_venir, {"code": ecran.code}, name=ecran.nom_url)
    for ecran in ECRANS
]
