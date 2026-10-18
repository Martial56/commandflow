import factory

from catalogue.models import Produit, Unite
from entreprises.tests.factories import EntrepriseFactory
from noyau.tests.factories import FabriqueModeleEntreprise


class ProduitFactory(FabriqueModeleEntreprise):
    class Meta:
        model = Produit

    entreprise = factory.SubFactory(EntrepriseFactory)
    code = factory.Sequence(lambda n: f"P{n:04d}")
    nom = factory.Sequence(lambda n: f"Produit {n}")
    unite = Unite.PIECE
    prix_reference = 250
