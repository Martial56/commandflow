import factory
from django.contrib.auth.models import Group

from comptes.models import Role, Utilisateur
from entreprises.tests.factories import EntrepriseFactory


class UtilisateurFactory(factory.django.DjangoModelFactory):
    """`UtilisateurFactory(roles=[Role.CHAUFFEUR])` ajoute l'utilisateur aux groupes."""

    class Meta:
        model = Utilisateur
        skip_postgeneration_save = True

    username = factory.Sequence(lambda n: f"utilisateur{n}")
    email = factory.LazyAttribute(lambda u: f"{u.username}@exemple.ci")
    telephone = factory.Sequence(lambda n: f"+22507{n:08d}")
    entreprise = factory.SubFactory(EntrepriseFactory)
    password = factory.django.Password("motdepasse-test")

    @factory.post_generation
    def roles(self, create, roles):
        if not create or not roles:
            return
        self.groups.add(*Group.objects.filter(name__in=[Role(r).label for r in roles]))
        if not self.role_principal:
            self.role_principal = Role(roles[0])
            self.save(update_fields=["role_principal"])
