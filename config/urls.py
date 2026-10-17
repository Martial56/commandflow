from django.contrib import admin
from django.urls import include, path

from comptes.views import accueil

urlpatterns = [
    path("", accueil, name="accueil"),
    path("admin/", admin.site.urls),
    path("comptes/", include("allauth.urls")),
    path("", include("noyau.urls")),
]
