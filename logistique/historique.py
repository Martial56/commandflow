"""Entrées « chargement » et « livraison » de l'historique d'une commande (maquette 10).

Reconstruites depuis django-pghistory, comme le reste de l'historique
(`commandes.historique`) : affectation, contrôle, écart, réception, retrait, arrivée,
résultat de la livraison, report, reliquats, retours et décisions d'exception. Rien
n'est saisi en double.
"""

from django.contrib.contenttypes.models import ContentType

from noyau.models import HistoriqueValidation, Validation
from noyau.templatetags.format_fr import date_courte, quantite

from .models import (
    Chargement,
    DecisionRetour,
    EtatRetour,
    HistoriqueChargement,
    HistoriqueLivraison,
    HistoriqueReliquat,
    HistoriqueRetour,
    Livraison,
    MotifEchec,
    StatutChargement,
    StatutLivraison,
    Tournee,
)

TEXTES_STATUT = {
    StatutChargement.EN_VERIFICATION: "Contrôle du chargement commencé",
    StatutChargement.VERIFIEE_PREPARATION: "Chargement vérifié par la préparation",
    StatutChargement.ECART_CONSTATE: "Écart constaté au chargement",
    StatutChargement.RECUE_CHAUFFEUR: "Réception confirmée par le livreur",
}
OBJETS_EXCEPTION = {
    "ecart": "l'écart au chargement",
    "depart": "le départ sans réception",
    "reception": "la réception saisie pour le prestataire",
    "livraison": "la livraison saisie pour le prestataire",
    "solde_reliquat": "le solde du reliquat",
}
MOTIFS = dict(MotifEchec.choices)
ETATS_RETOUR = dict(EtatRetour.choices)
DECISIONS_RETOUR = dict(DecisionRetour.choices)


def evenements(commande, auteur):
    """(date, ordre, auteur, texte) des chargements de la commande, dans l'ordre."""
    historique = list(
        HistoriqueChargement.objects.filter(commande_id=commande.pk).order_by("pgh_id")
    )
    numeros = dict(
        Tournee.toutes_entreprises.filter(pk__in={e.tournee_id for e in historique}).values_list(
            "pk", "numero"
        )
    )
    resultat = []
    precedents = {}
    for evenement in historique:
        tournee = numeros.get(evenement.tournee_id, "")
        ancien = precedents.get(evenement.pgh_obj_id)
        precedents[evenement.pgh_obj_id] = evenement
        if ancien is None:
            texte = f"Affectée à la tournée {tournee} (arrêt {evenement.ordre_passage})"
        elif ancien.statut == evenement.statut:
            continue
        elif evenement.statut == StatutChargement.RETIRE:
            texte = f"Retirée de la tournée {tournee}"
        elif evenement.statut == StatutChargement.ECART_CONSTATE and evenement.motif_contestation:
            texte = f"Écart signalé par le livreur (« {evenement.motif_contestation} »)"
        elif ancien.statut == StatutChargement.ECART_CONSTATE and (
            evenement.statut == StatutChargement.EN_VERIFICATION
        ):
            texte = "Marchandise complétée : nouveau contrôle"
        elif evenement.statut in TEXTES_STATUT:
            texte = TEXTES_STATUT[evenement.statut]
        else:
            continue
        resultat.append(
            (evenement.pgh_created_at, evenement.pgh_id, auteur(evenement.modifie_par_id), texte)
        )
    resultat += _decisions(Chargement, {e.pgh_obj_id for e in historique}, auteur)
    livraisons = set(Livraison.objects.filter(commande_id=commande.pk).values_list("pk", flat=True))
    resultat += _livraisons(commande, auteur)
    resultat += _decisions(Livraison, livraisons, auteur)
    resultat += _reliquats(commande, auteur)
    resultat += _retours(livraisons, auteur)
    return resultat


def _motif(evenement):
    return MOTIFS.get(evenement.motif_echec, "").lower()


def _texte_livraison(evenement):
    statut = evenement.statut
    if statut == StatutLivraison.SUR_PLACE:
        return "Livreur arrivé chez le client"
    if statut == StatutLivraison.LIVREE:
        nom = evenement.receptionnaire_nom
        return f"Livraison effectuée (reçue par {nom})" if nom else "Livraison effectuée"
    if statut == StatutLivraison.PARTIELLEMENT_LIVREE:
        return f"Livraison partielle ({_motif(evenement)}), avec l'accord du client"
    if statut == StatutLivraison.ECHOUEE:
        return f"Livraison non effectuée : {_motif(evenement)}"
    if statut == StatutLivraison.REPORTEE:
        return f"Livraison reportée au {date_courte(evenement.reportee_au)}"
    return None


def _livraisons(commande, auteur):
    """Arrivée et résultat de chaque livraison."""
    resultat = []
    precedents = {}
    for evenement in HistoriqueLivraison.objects.filter(commande_id=commande.pk).order_by("pgh_id"):
        ancien = precedents.get(evenement.pgh_obj_id)
        precedents[evenement.pgh_obj_id] = evenement
        if ancien is None or ancien.statut == evenement.statut:
            continue
        texte = _texte_livraison(evenement)
        if texte:
            resultat.append(
                (
                    evenement.pgh_created_at,
                    evenement.pgh_id,
                    auteur(evenement.modifie_par_id),
                    texte,
                )
            )
    return resultat


def _reliquats(commande, auteur):
    """Reliquat noté, planifié, soldé."""
    resultat = []
    precedents = {}
    for evenement in (
        HistoriqueReliquat.objects.filter(commande_origine_id=commande.pk)
        .select_related("ligne_commande__produit")
        .order_by("pgh_id")
    ):
        ancien = precedents.get(evenement.pgh_obj_id)
        precedents[evenement.pgh_obj_id] = evenement
        objet = f"{quantite(evenement.quantite)} {evenement.ligne_commande.produit.nom}"
        if ancien is None:
            texte = f"Reliquat noté : {objet}"
        elif evenement.solde and not ancien.solde:
            texte = f"Reliquat soldé : {objet}"
        elif evenement.nouvelle_date and evenement.nouvelle_date != ancien.nouvelle_date:
            texte = f"Reliquat planifié pour le {date_courte(evenement.nouvelle_date)} : {objet}"
        else:
            continue
        resultat.append(
            (evenement.pgh_created_at, evenement.pgh_id, auteur(evenement.modifie_par_id), texte)
        )
    return resultat


def _retours(livraisons, auteur):
    """Retour déclaré, puis décision du responsable."""
    resultat = []
    precedents = {}
    for evenement in (
        HistoriqueRetour.objects.filter(livraison_id__in=livraisons)
        .select_related("produit")
        .order_by("pgh_id")
    ):
        ancien = precedents.get(evenement.pgh_obj_id)
        precedents[evenement.pgh_obj_id] = evenement
        objet = f"{quantite(evenement.quantite)} {evenement.produit.nom}"
        if ancien is None:
            etat = ETATS_RETOUR.get(evenement.etat, "").lower()
            texte = f"Retour déclaré : {objet} ({etat}, « {evenement.motif} »)"
        elif evenement.decision and not ancien.decision:
            texte = f"Retour {objet} : {DECISIONS_RETOUR[evenement.decision].lower()}"
        else:
            continue
        resultat.append(
            (evenement.pgh_created_at, evenement.pgh_id, auteur(evenement.modifie_par_id), texte)
        )
    return resultat


def _decisions(modele, objets, auteur):
    """Demandes et décisions d'exception sur les chargements ou les livraisons de la
    commande."""
    resultat = []
    precedents = {}
    for evenement in HistoriqueValidation.objects.filter(
        content_type=ContentType.objects.get_for_model(modele),
        object_id__in=objets,
        action=Validation.Action.EXCEPTION,
    ).order_by("pgh_id"):
        objet = OBJETS_EXCEPTION.get((evenement.contenu or {}).get("objet"), "une exception")
        ancien = precedents.get(evenement.pgh_obj_id)
        precedents[evenement.pgh_obj_id] = evenement
        if ancien is None:
            motif = f" : « {evenement.motif} »" if evenement.motif else ""
            texte, qui = f"Demande de validation pour {objet}{motif}", evenement.demandee_par_id
        elif ancien.decision != evenement.decision:
            decision = (
                "acceptée" if evenement.decision == Validation.Decision.ACCEPTEE else "refusée"
            )
            texte, qui = f"Exception pour {objet} {decision}", evenement.validee_par_id
        else:
            continue
        resultat.append((evenement.pgh_created_at, evenement.pgh_id, auteur(qui), texte))
    return resultat
