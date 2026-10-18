from .navigation import menus


def navigation(request):
    """Menus de la barre latérale, calculés côté serveur selon les droits."""
    utilisateur = getattr(request, "user", None)
    if utilisateur is None or not utilisateur.is_authenticated:
        return {}
    return {"menus": menus(utilisateur)}
