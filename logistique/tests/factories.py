import datetime

import factory

from commandes.tests.factories import CommandeFactory
from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from logistique.models import Chargement, LigneChargement, Prestataire, Tournee, Vehicule
from noyau.tests.factories import FabriqueModeleEntreprise


class VehiculeFactory(FabriqueModeleEntreprise):
    class Meta:
        model = Vehicule

    entreprise = factory.SubFactory(EntrepriseFactory)
    immatriculation = factory.Sequence(lambda n: f"AB-{n:04d}-CI")
    libelle = "camionnette"


class PrestataireFactory(FabriqueModeleEntreprise):
    class Meta:
        model = Prestataire

    entreprise = factory.SubFactory(EntrepriseFactory)
    nom = factory.Sequence(lambda n: f"Express Livr. {n}")


class TourneeFactory(FabriqueModeleEntreprise):
    class Meta:
        model = Tournee

    entreprise = factory.SubFactory(EntrepriseFactory)
    numero = factory.Sequence(lambda n: f"TRN-2026-{n:06d}")
    date = factory.LazyFunction(lambda: datetime.date.today() + datetime.timedelta(days=1))
    chauffeur = factory.SubFactory(
        UtilisateurFactory,
        entreprise=factory.SelfAttribute("..entreprise"),
        roles=[Role.CHAUFFEUR],
    )


class ChargementFactory(FabriqueModeleEntreprise):
    class Meta:
        model = Chargement

    tournee = factory.SubFactory(TourneeFactory)
    entreprise = factory.SelfAttribute("tournee.entreprise")
    commande = factory.SubFactory(
        CommandeFactory,
        adresse__client__entreprise=factory.SelfAttribute("....entreprise"),
        date_livraison=factory.SelfAttribute("..tournee.date"),
    )


class LigneChargementFactory(FabriqueModeleEntreprise):
    class Meta:
        model = LigneChargement

    chargement = factory.SubFactory(ChargementFactory)
    entreprise = factory.SelfAttribute("chargement.entreprise")
    ligne_commande = factory.LazyAttribute(
        lambda o: o.chargement.commande.lignes.model.toutes_entreprises.filter(
            commande=o.chargement.commande
        ).first()
    )
    quantite_attendue = 100
