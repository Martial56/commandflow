"""Données de démonstration du Sprint 6 pour Mie Doré : une FNE en retard de paiement,
réglée en partie et sans son PDF, une livraison partielle encore à facturer et une avance
de client (maquettes 8, 15 et 16).

Tout passe par les services, sous l'identité de Mariam O. (Facturation) et de Serge A.
(Comptabilité). Appelé une seule fois, avec la tournée de démonstration du jour.
"""

import datetime

from facturation import services as facturation
from facturation.models import TypeDocument
from noyau.contexte import activer_utilisateur
from paiements import services as paiements
from paiements.models import ModePaiement


def charger(livree, jour, fiches, personne, sortie):
    with activer_utilisateur(personne["mariam.o"]):
        fne = facturation.enregistrer_choix(
            [livree],
            type_document=TypeDocument.FNE,
            numero_fne="FNE-2026-DEMO-0412",
            date_document=jour,
            # Échéance déjà passée : la facture apparaît en retard (maquette 16).
            echeance=jour - datetime.timedelta(days=5),
        )
    with activer_utilisateur(personne["serge.a"]):
        paiements.enregistrer_paiement(
            client=fne.client,
            montant=10000,
            mode=ModePaiement.MOBILE_MONEY,
            reference="MM-458210",
            affectations={fne: 10000},
        )
        paiements.enregistrer_paiement(
            client=fiches["Supermarché Prosuma Riviera"],
            montant=50000,
            mode=ModePaiement.VIREMENT,
            reference="VIR-2026-1105",
        )
    sortie.write(
        f"Facturation : {fne} (FNE, en retard, réglée en partie, PDF à joindre) ; "
        "une livraison partielle à facturer ; une avance de Prosuma."
    )
