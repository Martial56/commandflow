from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def accueil(request):
    """Page d'accueil provisoire ; remplacée par le tableau de bord à l'étape 10."""
    return render(request, "comptes/accueil.html")
