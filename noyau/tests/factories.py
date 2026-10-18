import factory


class FabriqueModeleEntreprise(factory.django.DjangoModelFactory):
    """Fabrique de base des `ModeleEntreprise`.

    Passe par `toutes_entreprises` : les données de test se créent sans entreprise
    courante. `save()` refuse toujours d'écrire dans une autre entreprise que la
    courante quand un contexte est actif.
    """

    class Meta:
        abstract = True

    @classmethod
    def _get_manager(cls, model_class):
        return model_class.toutes_entreprises
