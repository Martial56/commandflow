"""Historique des prix des produits (lot B9), reconstruit depuis `HistoriqueProduit`
(pghistory) : aucune saisie en double."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime

from .models import HistoriqueProduit

# Mise en place du suivi des produits (migration catalogue 0003) : les prix antérieurs
# d'un produit déjà existant ne sont pas connus.
DEBUT_HISTORIQUE = date(2026, 11, 18)


@dataclass(frozen=True)
class Prix:
    le: datetime
    prix: object
    auteur: object  # Utilisateur ou None (import, administration sans auteur)
    creation: bool


@dataclass(frozen=True)
class HistoriquePrix:
    prix: list  # du plus récent au plus ancien
    complet: bool  # faux si le produit existait avant le suivi (DEBUT_HISTORIQUE)


def historique_des_prix(produits):
    """{pk du produit: HistoriquePrix} pour les produits donnés (déjà filtrés sur
    l'entreprise courante). Seuls les changements de prix sont gardés : un changement de
    nom, de TVA ou une désactivation n'apparaît pas."""
    instantanes = defaultdict(list)
    for evenement in (
        HistoriqueProduit.objects.filter(pgh_obj__in=[p.pk for p in produits])
        .select_related("modifie_par")
        .order_by("pgh_obj_id", "pgh_id")
    ):
        instantanes[evenement.pgh_obj_id].append(evenement)

    resultat = {}
    for produit in produits:
        evenements = instantanes.get(produit.pk, [])
        prix, precedent = [], None
        for evenement in evenements:
            if evenement.prix_reference != precedent:
                prix.append(
                    Prix(
                        le=evenement.pgh_created_at,
                        prix=evenement.prix_reference,
                        auteur=evenement.modifie_par,
                        creation=evenement.pgh_label == "insert",
                    )
                )
                precedent = evenement.prix_reference
        complet = bool(evenements) and evenements[0].pgh_label == "insert"
        resultat[produit.pk] = HistoriquePrix(prix=prix[::-1], complet=complet)
    return resultat
