import datetime

import factory
from django.utils import timezone

from clients.tests.factories import ClientFactory
from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from facturation.models import Facture, TypeDocument
from noyau.tests.factories import FabriqueModeleEntreprise


class FactureFactory(FabriqueModeleEntreprise):
    """Facture FNE émise, sans livraison (les services créent les lignes)."""

    class Meta:
        model = Facture

    entreprise = factory.SubFactory(EntrepriseFactory)
    client = factory.SubFactory(ClientFactory, entreprise=factory.SelfAttribute("..entreprise"))
    numero_interne = factory.Sequence(lambda n: f"FAC-2026-{n:06d}")
    type_document = TypeDocument.FNE
    numero_fne = factory.Sequence(lambda n: f"FNE-{n:08d}")
    date_document = factory.LazyFunction(timezone.localdate)
    echeance = factory.LazyAttribute(lambda o: o.date_document + datetime.timedelta(days=30))
    montant_ht = 10000
    montant_ttc = 10000
    choix_fait_par = factory.SubFactory(
        UtilisateurFactory,
        entreprise=factory.SelfAttribute("..entreprise"),
        roles=[Role.FACTURATION],
    )
    choix_fait_le = factory.LazyFunction(timezone.now)
