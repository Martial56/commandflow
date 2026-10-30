"""Lectures sur la logistique utilisées par les conditions de transition des commandes."""

from decimal import Decimal

from django.contrib.contenttypes.models import ContentType

from noyau.models import Validation

from .models import Chargement, StatutChargement


def exceptions(chargement, objet, *decisions):
    """Demandes d'exception sur ce chargement, pour `objet` :
    « ecart », « depart » ou « reception »."""
    return Validation.objects.filter(
        content_type=ContentType.objects.get_for_model(Chargement),
        object_id=chargement.pk,
        action=Validation.Action.EXCEPTION,
        decision__in=decisions,
        contenu__objet=objet,
    )


def exception_acceptee(chargement, objet):
    return exceptions(chargement, objet, Validation.Decision.ACCEPTEE).exists()


def quantites_retenues(chargement):
    """Quantité chargée retenue par ligne : le préparé, sauf exception d'écart acceptée,
    dont les quantités font foi (décision 8.3 ; « prévu » de la livraison au Sprint 5)."""
    retenues = {ligne.pk: ligne.quantite_preparee for ligne in chargement.lignes.all()}
    derniere = (
        exceptions(chargement, "ecart", Validation.Decision.ACCEPTEE)
        .order_by("-decidee_le", "-pk")
        .first()
    )
    if derniere is not None:
        for pk, quantite in (derniere.contenu.get("lignes") or {}).items():
            if int(pk) in retenues:
                retenues[int(pk)] = Decimal(quantite)
    return retenues


def recapitulatif(tournee):
    """Récapitulatif du §10 : commandes attendues, vérifiées, en écart, reçues."""
    statuts = list(tournee.chargements_actifs().values_list("statut", flat=True))
    return {
        "attendues": len(statuts),
        "verifiees": sum(
            s in (StatutChargement.VERIFIEE_PREPARATION, StatutChargement.RECUE_CHAUFFEUR)
            for s in statuts
        ),
        "ecarts": statuts.count(StatutChargement.ECART_CONSTATE),
        "recues": statuts.count(StatutChargement.RECUE_CHAUFFEUR),
    }


def chargement_actif(commande):
    return (
        Chargement.objects.filter(commande=commande)
        .exclude(statut=StatutChargement.RETIRE)
        .select_related("tournee")
        .order_by("-pk")
        .first()
    )


def peut_partir(chargement):
    """Reçu par le livreur, ou départ autorisé par exception (§4, §5, décision 8.3)."""
    return chargement.statut == StatutChargement.RECUE_CHAUFFEUR or exception_acceptee(
        chargement, "depart"
    )


def commande_chargee(commande):
    """La commande peut partir en livraison : son chargement actif peut partir."""
    chargement = chargement_actif(commande)
    return chargement is not None and peut_partir(chargement)
