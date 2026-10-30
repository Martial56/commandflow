"""Entrées « chargement » de l'historique d'une commande (fiche, maquette 10).

Reconstruites depuis django-pghistory, comme le reste de l'historique
(`commandes.historique`) : affectation, contrôle, écart, réception, retrait et décisions
d'exception. Rien n'est saisi en double.
"""

from django.contrib.contenttypes.models import ContentType

from noyau.models import HistoriqueValidation, Validation

from .models import Chargement, HistoriqueChargement, StatutChargement, Tournee

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
}


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
    resultat += _decisions({e.pgh_obj_id for e in historique}, auteur)
    return resultat


def _decisions(chargements, auteur):
    """Demandes et décisions d'exception sur les chargements de la commande."""
    resultat = []
    precedents = {}
    for evenement in HistoriqueValidation.objects.filter(
        content_type=ContentType.objects.get_for_model(Chargement),
        object_id__in=chargements,
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
