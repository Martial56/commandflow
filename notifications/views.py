"""Page « Notifications » : celles de l'utilisateur connecté uniquement (§16.1)."""

from django.contrib.auth.decorators import login_required
from django.db.models import F
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from noyau.contexte import entreprise_courante

from . import services
from .models import Notification


@login_required
def liste(request):
    # Non lues d'abord (lue_le vide), puis les plus récentes.
    notifications = Notification.objects.filter(destinataire=request.user).order_by(
        F("lue_le").asc(nulls_first=True), "-pk"
    )[:100]
    return render(request, "notifications/liste.html", {"notifications": notifications})


@login_required
@require_POST
def ouvrir(request, pk):
    """Marque la notification lue et ouvre l'écran où la traiter."""
    notification = get_object_or_404(Notification, pk=pk, destinataire=request.user)
    services.marquer_lue(notification)
    if notification.lien and url_has_allowed_host_and_scheme(
        notification.lien, allowed_hosts={request.get_host()}
    ):
        return redirect(notification.lien)
    return redirect("notifications")


@login_required
@require_POST
def tout_lire(request):
    services.tout_marquer_lu(request.user)
    return redirect("notifications")


def nombre_non_lues(request):
    """Processeur de contexte : pastille de la cloche dans la barre de menus."""
    utilisateur = getattr(request, "user", None)
    # Pas d'entreprise courante (page de connexion, par exemple) : pas de pastille.
    if utilisateur is None or not utilisateur.is_authenticated or entreprise_courante() is None:
        return {}
    return {"notifications_non_lues": services.non_lues(utilisateur).count()}
