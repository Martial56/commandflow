from django.urls import path

from . import views

urlpatterns = [
    path("", views.liste, name="notifications"),
    path("<int:pk>/", views.ouvrir, name="notification_ouvrir"),
    path("tout-lire/", views.tout_lire, name="notifications_tout_lire"),
]
