import pghistory

from noyau.contexte import activer_entreprise, activer_utilisateur


class EntrepriseCouranteMiddleware:
    """Définit l'entreprise et l'utilisateur courants pour la durée de la requête.

    Ouvre aussi le contexte django-pghistory de la requête : les modifications
    suivies sont regroupées et rattachées à l'URL, à l'utilisateur et à l'entreprise.

    À placer après `AuthenticationMiddleware`.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        utilisateur = request.user if request.user.is_authenticated else None
        entreprise = utilisateur.entreprise if utilisateur is not None else None
        with (
            pghistory.context(url=request.path),
            activer_utilisateur(utilisateur),
            activer_entreprise(entreprise),
        ):
            return self.get_response(request)
