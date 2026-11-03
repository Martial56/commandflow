"""Lectures sur la logistique utilisées par les conditions de transition des commandes."""

from decimal import Decimal

from django.contrib.contenttypes.models import ContentType

from noyau.models import Validation

from .models import Chargement, StatutChargement, StatutLivraison


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


# Livraison traitée : le chargement n'est plus « en cours » (la commande peut être relancée
# ou son reliquat chargé dans une autre tournée).
STATUTS_LIVRAISON_TRAITEE = [
    StatutLivraison.LIVREE,
    StatutLivraison.PARTIELLEMENT_LIVREE,
    StatutLivraison.ECHOUEE,
    StatutLivraison.REPORTEE,
]


def chargements_en_cours():
    """Chargements dans une tournée, non retirés, dont la livraison n'est pas traitée."""
    return Chargement.objects.exclude(statut=StatutChargement.RETIRE).exclude(
        livraison__statut__in=STATUTS_LIVRAISON_TRAITEE
    )


def chargement_actif(commande):
    return (
        chargements_en_cours()
        .filter(commande=commande)
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


# --- Livraison (Sprint 5) -------------------------------------------------------------


def moyens_de_preuve(livraison):
    """Moyens de preuve réunis : preuves jointes, réceptionnaire, n° de bordereau, GPS."""
    moyens = set(livraison.preuves.values_list("type", flat=True))
    if livraison.receptionnaire_nom.strip():
        moyens.add("receptionnaire")
    if livraison.numero_bordereau.strip():
        moyens.add("numero_bordereau")
    if livraison.latitude is not None and livraison.longitude is not None:
        moyens.add("gps")
    return moyens


def preuves_manquantes(livraison):
    """Exigences de preuve non remplies, selon la catégorie du client (décision 8.4)."""
    from entreprises.parametres import preuves_obligatoires

    commande = livraison.commande
    moyens = moyens_de_preuve(livraison)
    return [
        exigence
        for exigence in preuves_obligatoires(commande.entreprise, commande.client.categorie)
        if not moyens.intersection(exigence)
    ]


def derniere_livraison(commande):
    from .models import Livraison

    return Livraison.objects.filter(commande=commande).order_by("-pk").first()


def reliquats_ouverts(commande):
    from .models import Reliquat

    return Reliquat.objects.filter(commande_origine=commande, traite=False)


def reliquats_par_produit(date_livraison):
    """Reliquats planifiés à cette date, non soldés : comptés dans le « commandé » du plan
    de production (décision du 3 novembre 2026)."""
    from django.db.models import Sum

    from .models import Reliquat

    return dict(
        Reliquat.objects.filter(nouvelle_date=date_livraison, solde=False)
        .values("ligne_commande__produit")
        .annotate(total=Sum("quantite"))
        .values_list("ligne_commande__produit", "total")
    )
