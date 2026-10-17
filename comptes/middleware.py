from allauth.mfa.models import Authenticator
from allauth.mfa.utils import is_mfa_enabled
from django.shortcuts import redirect
from django.urls import reverse

from .models import ROLES_DOUBLE_AUTHENTIFICATION
from .rules import a_le_role

_doit_avoir_double_authentification = a_le_role(*ROLES_DOUBLE_AUTHENTIFICATION)

# Chemins accessibles avant l'activation : activation 2FA, confirmation du mot de passe
# (demandée par allauth avant l'activation), déconnexion, fichiers statiques.
_PREFIXES_AUTORISES = (
    "/comptes/2fa/",
    "/comptes/reauthenticate/",
    "/comptes/logout/",
    "/static/",
)


class DoubleAuthentificationObligatoireMiddleware:
    """Oblige les profils Direction et Comptabilité à activer le code de vérification.

    Tant qu'aucune application d'authentification (TOTP) n'est activée, toute page
    redirige vers l'activation. Une fois activée, allauth demande le code à chaque
    connexion. À placer après `AuthenticationMiddleware`.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        utilisateur = request.user
        if (
            utilisateur.is_authenticated
            and not request.path.startswith(_PREFIXES_AUTORISES)
            and _doit_avoir_double_authentification(utilisateur)
            and not is_mfa_enabled(utilisateur, [Authenticator.Type.TOTP])
        ):
            return redirect(reverse("mfa_activate_totp"))
        return self.get_response(request)
