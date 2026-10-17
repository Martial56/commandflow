from noyau.contexte import activer_entreprise, activer_utilisateur


class EntrepriseCouranteMiddleware:
    """Définit l'entreprise et l'utilisateur courants pour la durée de la requête.

    À placer après `AuthenticationMiddleware`.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        utilisateur = request.user if request.user.is_authenticated else None
        entreprise = utilisateur.entreprise if utilisateur is not None else None
        with activer_utilisateur(utilisateur), activer_entreprise(entreprise):
            return self.get_response(request)
