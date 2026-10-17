import factory

from entreprises.models import Entreprise


class EntrepriseFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Entreprise
        django_get_or_create = ["slug"]

    nom = factory.Sequence(lambda n: f"Entreprise {n}")
    slug = factory.Sequence(lambda n: f"entreprise-{n}")
