from django.urls import path

from . import views
from .navigation import ECRANS

urlpatterns = [path("", views.accueil, name="accueil")]

# Écrans livrés : leurs URL sont dans leur app (config/urls.py), sous le même nom
# `ecran_<code>`, qui sert à la navigation.
ECRANS_LIVRES = {"clients", "commandes"}

# Écrans en attente de leur sprint : page « en préparation ».
urlpatterns += [
    path(ecran.chemin, views.ecran_a_venir, {"code": ecran.code}, name=ecran.nom_url)
    for ecran in ECRANS
    if ecran.code not in ECRANS_LIVRES
]
