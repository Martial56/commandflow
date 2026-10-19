import factory

from clients.models import Adresse, Categorie, Client, ContactClient
from entreprises.tests.factories import EntrepriseFactory
from noyau.tests.factories import FabriqueModeleEntreprise


class ClientFactory(FabriqueModeleEntreprise):
    class Meta:
        model = Client

    entreprise = factory.SubFactory(EntrepriseFactory)
    code = factory.Sequence(lambda n: f"CLI-{n:06d}")
    nom = factory.Sequence(lambda n: f"Client {n}")
    categorie = Categorie.RESTAURANT
    telephone = factory.Sequence(lambda n: f"07 {n:08d}")


class AdresseFactory(FabriqueModeleEntreprise):
    class Meta:
        model = Adresse

    client = factory.SubFactory(ClientFactory)
    entreprise = factory.SelfAttribute("client.entreprise")
    libelle = "Cuisine principale"
    adresse = "Rue des Jardins"
    commune = "Cocody"


class ContactFactory(FabriqueModeleEntreprise):
    class Meta:
        model = ContactClient

    client = factory.SubFactory(ClientFactory)
    entreprise = factory.SelfAttribute("client.entreprise")
    nom = "M. Koffi"
