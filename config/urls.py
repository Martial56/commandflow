from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("comptes/", include("allauth.urls")),
    path("", include("noyau.urls")),
    path("clients/", include("clients.urls")),
    path("", include("comptes.urls")),
]
