"""Bloc « Livraisons » de la fiche commande (maquette 10) : chaque tentative avec ses
quantités, son réceptionnaire, ses preuves (lien signé), l'accord du client, les reliquats
et les retours. Lecture seule ; les actions restent sur les écrans Tournées et Ma tournée.
"""

from django.db.models import Prefetch

from noyau.fichiers import url_signee

from .models import Chargement, LigneLivraison, Livraison, Reliquat, StatutChargement


def dernier_chargement(commande):
    """Dernier chargement non retiré, même livré : la tournée affichée sur la fiche."""
    return (
        Chargement.objects.filter(commande=commande)
        .exclude(statut=StatutChargement.RETIRE)
        .select_related("tournee__chauffeur", "tournee__prestataire")
        .order_by("-pk")
        .first()
    )


def livraisons(commande, utilisateur):
    """Livraisons de la commande, de la plus ancienne à la plus récente."""
    resultat = list(
        Livraison.objects.filter(commande=commande)
        .select_related("chargement__tournee")
        .prefetch_related(
            Prefetch(
                "lignes",
                queryset=LigneLivraison.objects.select_related("ligne_commande__produit").order_by(
                    "pk"
                ),
            ),
            "accords",
            "retours__produit",
            "retours__decide_par",
            Prefetch(
                "reliquats",
                queryset=Reliquat.objects.select_related("ligne_commande__produit").order_by("pk"),
            ),
        )
        .order_by("pk")
    )
    for livraison in resultat:
        livraison.liste_preuves = [
            (preuve, url_signee(preuve.piece, utilisateur) if preuve.piece_id else None)
            for preuve in livraison.preuves.select_related("piece").order_by("pk")
        ]
    return resultat
