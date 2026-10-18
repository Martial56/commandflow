from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render

from .navigation import ECRANS_PAR_CODE, ecran_arrivee


@login_required
def accueil(request):
    """Redirige vers l'écran d'arrivée du rôle de l'utilisateur."""
    utilisateur = request.user
    if utilisateur.is_superuser and utilisateur.entreprise_id is None:
        # Superutilisateur de la plateforme : aucune donnée métier, administration seulement.
        return redirect("admin:index")
    ecran = ecran_arrivee(utilisateur)
    if ecran is None:
        return render(request, "comptes/aucun_acces.html", status=403)
    return redirect(ecran.nom_url)


@login_required
def ecran_a_venir(request, code):
    """Écran dont le contenu arrive dans un sprint ultérieur ; l'accès est déjà contrôlé."""
    ecran = ECRANS_PAR_CODE[code]
    if not request.user.has_perm(ecran.permission):
        raise PermissionDenied
    gabarit = "comptes/ecran_terrain.html" if ecran.terrain else "comptes/ecran_bureau.html"
    return render(request, gabarit, {"ecran": ecran})
