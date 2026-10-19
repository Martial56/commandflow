import datetime

import factory

from catalogue.tests.factories import ProduitFactory
from clients.tests.factories import AdresseFactory
from commandes.models import Canal, Commande, LigneCommande
from noyau.tests.factories import FabriqueModeleEntreprise


class CommandeFactory(FabriqueModeleEntreprise):
    """Commande au statut `statut` (brouillon par défaut), avec une ligne si `avec_ligne`."""

    class Meta:
        model = Commande
        skip_postgeneration_save = True

    adresse = factory.SubFactory(AdresseFactory, par_defaut=True)
    client = factory.SelfAttribute("adresse.client")
    entreprise = factory.SelfAttribute("adresse.entreprise")
    numero = factory.Sequence(lambda n: f"CMD-2026-{n:06d}")
    canal = Canal.WHATSAPP
    date_livraison = factory.LazyFunction(lambda: datetime.date.today() + datetime.timedelta(1))
    creneau = "06h–08h"

    @factory.post_generation
    def avec_ligne(self, create, valeur):
        if create and valeur is not False:
            LigneCommandeFactory(commande=self)


class LigneCommandeFactory(FabriqueModeleEntreprise):
    class Meta:
        model = LigneCommande

    commande = factory.SubFactory(CommandeFactory, avec_ligne=False)
    entreprise = factory.SelfAttribute("commande.entreprise")
    produit = factory.SubFactory(
        ProduitFactory, entreprise=factory.SelfAttribute("..entreprise"), prix_reference=250
    )
    quantite = 10
