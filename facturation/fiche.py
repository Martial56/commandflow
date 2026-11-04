"""Ligne « Facture » de la fiche commande (maquette 10) : documents émis ou annulés qui
couvrent ses livraisons."""

from .models import Facture


def factures(commande):
    return list(
        Facture.objects.filter(lignes__livraison__commande=commande)
        .distinct()
        .order_by("date_document", "pk")
    )
