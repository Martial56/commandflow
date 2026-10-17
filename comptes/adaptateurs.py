from allauth.account.adapter import DefaultAccountAdapter
from allauth.account.fields import PhoneField

from .models import Utilisateur


def normaliser_telephone(valeur):
    """« +225 07 00 00 00 01 » ou « +225-07… » → « +2250700000001 »."""
    return (valeur or "").replace(" ", "").replace("-", "").replace(".", "")


class ChampTelephone(PhoneField):
    """Retire espaces, tirets et points avant de vérifier le format international."""

    def to_python(self, value):
        return normaliser_telephone(super().to_python(value))


class AdaptateurCompte(DefaultAccountAdapter):
    """Connexion par téléphone ou e-mail ; comptes créés par un responsable uniquement."""

    def is_open_for_signup(self, request):
        return False

    # Téléphone : stocké dans Utilisateur.telephone, considéré comme vérifié (saisi par
    # un responsable). Aucune vérification par SMS au MVP.

    def get_phone(self, user):
        return (user.telephone, True) if user.telephone else None

    def set_phone(self, user, phone, verified):
        user.telephone = phone
        user.save(update_fields=["telephone"])

    def set_phone_verified(self, user, phone):
        pass

    def phone_form_field(self, **kwargs):
        return ChampTelephone(**kwargs)

    def get_user_by_phone(self, phone):
        # Le formulaire de connexion transmet la saisie brute.
        return Utilisateur.objects.filter(telephone=normaliser_telephone(phone)).first()
