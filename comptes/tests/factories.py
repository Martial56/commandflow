import factory

from comptes.models import Utilisateur
from entreprises.tests.factories import EntrepriseFactory


class UtilisateurFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Utilisateur
        skip_postgeneration_save = True

    username = factory.Sequence(lambda n: f"utilisateur{n}")
    telephone = factory.Sequence(lambda n: f"+22507{n:08d}")
    entreprise = factory.SubFactory(EntrepriseFactory)
    password = factory.django.Password("motdepasse-test")
