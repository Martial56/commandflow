"""Historique et parcours d'une commande (maquette 10), reconstruits depuis django-pghistory.

Rien n'est saisi en double : chaque entrée provient des instantanés de la commande, de
ses lignes et des validations. Une modification ou une annulation appliquée lors de
l'acceptation d'une demande est rattachée à cette décision (même contexte pghistory) :
« Croissant : 30 → 40, validé par Ibrahim T. (« demande client par WhatsApp ») ».
"""

from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType

from catalogue.models import Produit
from noyau.models import HistoriqueValidation, Validation
from noyau.templatetags.format_fr import date_courte, quantite

from .models import Commande, HistoriqueCommande, HistoriqueLigneCommande, Statut

ETAPES = [
    (Statut.CONFIRMEE, "Confirmée"),
    (Statut.A_PLANIFIER, "Planifiée"),
    (Statut.EN_PREPARATION, "En préparation"),
    (Statut.PRETE_CHARGEMENT, "Prête à charger"),
    (Statut.EN_LIVRAISON, "En livraison"),
    (Statut.LIVREE, "Livrée"),
]

# Complément de « Demande … » (article élidé devant une voyelle).
LIBELLES_ACTION = {
    Validation.Action.MODIFICATION: "de modification",
    Validation.Action.ANNULATION: "d'annulation",
    Validation.Action.URGENCE: "d'urgence",
}


@dataclass
class Entree:
    date: object
    ordre: int
    auteur: str
    texte: str


@dataclass
class Etape:
    libelle: str
    date: object = None

    @property
    def atteinte(self):
        return self.date is not None


def parcours(commande):
    """Étapes de la frise, avec la date à laquelle chacune a été atteinte."""
    atteintes = {}
    for statut, date in (
        HistoriqueCommande.objects.filter(pgh_obj=commande)
        .order_by("pgh_id")
        .values_list("statut", "pgh_created_at")
    ):
        atteintes.setdefault(statut, date)
    return [Etape(libelle, atteintes.get(statut)) for statut, libelle in ETAPES]


def historique(commande):
    """Entrées de l'historique, des plus récentes aux plus anciennes."""
    utilisateurs = {u.pk: str(u) for u in get_user_model().objects.all()}

    def auteur(identifiant):
        return utilisateurs.get(identifiant, "Système")

    entrees = []
    decisions = _decisions(commande, entrees, auteur)

    evenements = list(HistoriqueCommande.objects.filter(pgh_obj=commande).order_by("pgh_id"))
    contexte_creation = evenements[0].pgh_context_id if evenements else None
    precedent = None
    for evenement in evenements:
        texte = _texte_commande(precedent, evenement)
        precedent = evenement
        if texte:
            entrees.append(_entree(evenement, auteur(evenement.modifie_par_id), texte, decisions))

    _lignes(commande, entrees, auteur, decisions, contexte_creation)

    for decision in decisions.values():
        if not decision["rattachee"]:
            entrees.append(
                Entree(decision["date"], decision["ordre"], decision["auteur"], decision["texte"])
            )
    return sorted(entrees, key=lambda e: (e.date, e.ordre), reverse=True)


def _entree(evenement, nom, texte, decisions):
    decision = decisions.get(evenement.pgh_context_id)
    if decision is not None:
        decision["rattachee"] = True
        texte = f"{texte}, validé par {decision['auteur']} (« {decision['motif']} »)"
    return Entree(evenement.pgh_created_at, evenement.pgh_id, nom, texte)


def _decisions(commande, entrees, auteur):
    """Demandes et décisions de validation. Renvoie les acceptations dont l'effet
    (modification, annulation) apparaît dans les autres entrées, par contexte."""
    decisions = {}
    precedent = {}
    for evenement in HistoriqueValidation.objects.filter(
        content_type=ContentType.objects.get_for_model(Commande), object_id=commande.pk
    ).order_by("pgh_id"):
        action = LIBELLES_ACTION.get(evenement.action, f"de {evenement.action}")
        ancien = precedent.get(evenement.pgh_obj_id)
        precedent[evenement.pgh_obj_id] = evenement
        if ancien is None:
            motif = f" : « {evenement.motif} »" if evenement.motif else ""
            texte = (
                "Demande de validation de l'urgence"
                if evenement.action == Validation.Action.URGENCE
                else f"Demande {action}{motif}"
            )
            entrees.append(
                Entree(
                    evenement.pgh_created_at,
                    evenement.pgh_id,
                    auteur(evenement.demandee_par_id),
                    texte,
                )
            )
        elif ancien.decision != evenement.decision:
            decideur = auteur(evenement.validee_par_id)
            if evenement.decision == Validation.Decision.REFUSEE:
                entrees.append(
                    Entree(
                        evenement.pgh_created_at,
                        evenement.pgh_id,
                        decideur,
                        f"Demande {action} refusée",
                    )
                )
            elif evenement.action == Validation.Action.URGENCE:
                entrees.append(
                    Entree(evenement.pgh_created_at, evenement.pgh_id, decideur, "Urgence validée")
                )
            else:
                decisions[evenement.pgh_context_id] = {
                    "auteur": decideur,
                    "motif": evenement.motif,
                    "date": evenement.pgh_created_at,
                    "ordre": evenement.pgh_id,
                    "texte": f"Demande {action} acceptée",
                    "rattachee": False,
                }
    return decisions


def _texte_commande(precedent, evenement):
    if precedent is None:
        if evenement.modele_recurrent_id:
            from .models import ModeleRecurrent

            modele = ModeleRecurrent.toutes_entreprises.filter(pk=evenement.modele_recurrent_id)
            return f"Générée depuis le modèle récurrent « {modele.first()} »"
        return "Commande créée"

    changements = []
    if precedent.statut != evenement.statut:
        changements.append(_texte_statut(evenement, precedent.statut))
    if precedent.date_livraison != evenement.date_livraison:
        changements.append(
            f"Livraison : {date_courte(precedent.date_livraison)} → "
            f"{date_courte(evenement.date_livraison)}"
        )
    if precedent.creneau != evenement.creneau:
        changements.append(f"Créneau : {precedent.creneau} → {evenement.creneau}")
    if precedent.adresse_id != evenement.adresse_id:
        changements.append("Adresse de livraison modifiée")
    if precedent.instructions != evenement.instructions:
        changements.append("Instructions modifiées")
    if precedent.urgente != evenement.urgente:
        changements.append("Marquée urgente" if evenement.urgente else "Urgence retirée")
    return " ; ".join(changements)


def _texte_statut(evenement, precedent_statut=None):
    if evenement.statut == Statut.A_CONFIRMER:
        return "Soumise pour confirmation"
    if evenement.statut == Statut.CONFIRMEE:
        note = f" (« {evenement.note_confirmation} »)" if evenement.note_confirmation else ""
        return f"Commande confirmée avec le client{note}"
    if evenement.statut == Statut.ANNULEE:
        return f"Commande annulée (« {evenement.motif_annulation} »)"
    if evenement.statut == Statut.A_PLANIFIER:
        if precedent_statut == Statut.EN_PREPARATION:
            return "Reportée vers un plan non validé : retour « À planifier »"
        return f"Intégrée au plan de production du {date_courte(evenement.date_livraison)}"
    if evenement.statut == Statut.EN_PREPARATION:
        return "Plan de production validé : passage en préparation"
    if evenement.statut == Statut.PRETE_CHARGEMENT:
        return "Prête au chargement : quantités réservées"
    return f"Passage au statut « {Statut(evenement.statut).label} »"


def _lignes(commande, entrees, auteur, decisions, contexte_creation):
    """Ajouts, retraits et changements de quantité après la création de la commande."""
    evenements = list(
        HistoriqueLigneCommande.objects.filter(commande_id=commande.pk).order_by("pgh_id")
    )
    noms = dict(
        Produit.toutes_entreprises.filter(pk__in={e.produit_id for e in evenements}).values_list(
            "pk", "nom"
        )
    )
    precedents = {}
    for evenement in evenements:
        nom = noms.get(evenement.produit_id, "Produit")
        ancien = precedents.get(evenement.pgh_obj_id)
        precedents[evenement.pgh_obj_id] = evenement
        if ancien is None:
            if evenement.pgh_context_id == contexte_creation:
                continue  # lignes saisies à la création
            texte = f"Ajout : {nom} × {quantite(evenement.quantite)}"
        elif not ancien.retiree and evenement.retiree:
            texte = f"Retrait : {nom}"
        elif ancien.quantite != evenement.quantite:
            texte = f"{nom} : {quantite(ancien.quantite)} → {quantite(evenement.quantite)}"
        else:
            continue
        entrees.append(_entree(evenement, auteur(evenement.modifie_par_id), texte, decisions))
