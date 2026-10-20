import datetime

import factory

from catalogue.tests.factories import ProduitFactory
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from noyau.tests.factories import FabriqueModeleEntreprise
from production.models import BesoinProduit, PlanProduction, SaisieProduction


class PlanProductionFactory(FabriqueModeleEntreprise):
    class Meta:
        model = PlanProduction

    entreprise = factory.SubFactory(EntrepriseFactory)
    date_livraison = factory.LazyFunction(lambda: datetime.date.today() + datetime.timedelta(1))


class BesoinProduitFactory(FabriqueModeleEntreprise):
    class Meta:
        model = BesoinProduit

    plan = factory.SubFactory(PlanProductionFactory)
    entreprise = factory.SelfAttribute("plan.entreprise")
    produit = factory.SubFactory(ProduitFactory, entreprise=factory.SelfAttribute("..entreprise"))
    quantite_commandee = 100
    quantite_a_produire = 100


class SaisieProductionFactory(FabriqueModeleEntreprise):
    class Meta:
        model = SaisieProduction

    besoin = factory.SubFactory(BesoinProduitFactory)
    entreprise = factory.SelfAttribute("besoin.entreprise")
    lot = factory.Sequence(lambda n: f"Lot {n + 1}")
    quantite = 50
    saisie_par = factory.SubFactory(
        UtilisateurFactory, entreprise=factory.SelfAttribute("..entreprise")
    )
