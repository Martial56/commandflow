"""Services des paiements (§7 `affecter_paiement`, §15, §22-24 ; décisions 8.5).

Le solde d'une facture = montant TTC − affectations des paiements non annulés. Son statut
de paiement (non payé, partiellement payé, payé, en retard) est calculé, jamais saisi ; le
marquage « contesté / à vérifier » de la facture s'y ajoute sans le remplacer.
"""

from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied
from django.db import models, transaction
from django.db.models import DecimalField, F, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from clients.models import Client
from facturation.models import Facture, StatutFacture
from noyau.contexte import entreprise_obligatoire, utilisateur_courant
from noyau.models import PieceJointe
from noyau.services import joindre_fichier

from .models import AffectationPaiement, ModePaiement, Paiement

ZERO = Decimal(0)
TYPES_JUSTIFICATIF = {"application/pdf"}


class StatutPaiement(models.TextChoices):
    NON_PAYE = "non_paye", "Non payé"
    PARTIEL = "partiel", "Partiellement payé"
    PAYE = "paye", "Payé"
    EN_RETARD = "en_retard", "En retard"


class ActionImpossible(Exception):
    """Action refusée par une règle métier ; le message s'affiche tel quel."""


def _utilisateur():
    utilisateur = utilisateur_courant()
    if utilisateur is None:
        raise PermissionDenied("Action à attribuer à un utilisateur connecté.")
    return utilisateur


def _exiger(permission, objet=None):
    if not _utilisateur().has_perm(permission, objet):
        raise PermissionDenied("Votre rôle ne permet pas cette action.")


def _montant(valeur):
    try:
        montant = Decimal(str(valeur).replace(" ", "").replace(" ", ""))
    except (InvalidOperation, TypeError, ValueError):
        raise ActionImpossible("Montant invalide.") from None
    if not montant.is_finite() or montant != montant.to_integral_value():
        raise ActionImpossible("Montant invalide (en francs CFA, sans décimale).")
    return montant


# --- Soldes et statuts (calculés) ----------------------------------------------------

_REGLE = Coalesce(
    Sum("affectations__montant", filter=Q(affectations__paiement__annule=False)),
    Value(ZERO),
    output_field=DecimalField(max_digits=14, decimal_places=0),
)


def factures_avec_solde():
    """Factures émises annotées de `regle` (affecté, paiements non annulés) et `solde`."""
    return (
        Facture.objects.filter(statut=StatutFacture.EMISE)
        .annotate(regle=_REGLE)
        .annotate(solde=F("montant_ttc") - F("regle"))
    )


def solde(facture):
    """Reste dû sur une facture émise (0 pour un document annulé)."""
    if facture.statut != StatutFacture.EMISE:
        return ZERO
    regle = (
        AffectationPaiement.objects.filter(facture=facture, paiement__annule=False).aggregate(
            total=Sum("montant")
        )["total"]
        or ZERO
    )
    return facture.montant_ttc - regle


def statut_paiement(facture, aujourd_hui=None, reste=None):
    """Non payé, partiellement payé, payé, ou en retard (échéance dépassée, solde > 0).
    Un paiement partiel ne marque jamais la facture « payée » (§22-24)."""
    aujourd_hui = aujourd_hui or timezone.localdate()
    reste = solde(facture) if reste is None else reste
    if reste <= 0:
        return StatutPaiement.PAYE
    if facture.echeance < aujourd_hui:
        return StatutPaiement.EN_RETARD
    if reste < facture.montant_ttc:
        return StatutPaiement.PARTIEL
    return StatutPaiement.NON_PAYE


def factures_ouvertes(client):
    """Factures du client restant à payer, la plus ancienne échéance d'abord."""
    return (
        factures_avec_solde()
        .filter(client=client, solde__gt=0)
        .order_by("echeance", "date_document", "pk")
    )


def disponible(paiement):
    """Part d'un paiement non encore affectée : l'avance du client (décision 8.5)."""
    if paiement.annule:
        return ZERO
    affecte = paiement.affectations.aggregate(total=Sum("montant"))["total"] or ZERO
    return paiement.montant - affecte


def avances(client):
    """Total des avances du client (paiements non annulés non entièrement affectés)."""
    return sum(
        (disponible(paiement) for paiement in Paiement.objects.filter(client=client, annule=False)),
        ZERO,
    )


def balance_agee(aujourd_hui=None):
    """Créances par client et par ancienneté (maquette 16) : non échu, 1–30 j, +30 j."""
    aujourd_hui = aujourd_hui or timezone.localdate()
    lignes = defaultdict(lambda: {"non_echu": ZERO, "j30": ZERO, "plus30": ZERO, "total": ZERO})
    for facture in factures_avec_solde().filter(solde__gt=0).select_related("client"):
        retard = (aujourd_hui - facture.echeance).days
        colonne = "non_echu" if retard <= 0 else ("j30" if retard <= 30 else "plus30")
        ligne = lignes[facture.client]
        ligne[colonne] += facture.solde
        ligne["total"] += facture.solde
    return sorted(lignes.items(), key=lambda element: -element[1]["total"])


# --- Écritures -----------------------------------------------------------------------


def _affecter(paiement, facture, montant):
    """Contrôles communs : même client, facture émise, montant ≤ solde et ≤ disponible."""
    montant = _montant(montant)
    if montant <= 0:
        raise ActionImpossible("Montant affecté invalide.")
    facture = Facture.objects.select_for_update().get(pk=facture.pk)
    if facture.client_id != paiement.client_id:
        raise ActionImpossible(f"{facture} n'est pas une facture de {paiement.client}.")
    if facture.statut != StatutFacture.EMISE:
        raise ActionImpossible(f"{facture} est annulée.")
    reste = solde(facture)
    if montant > reste:
        raise ActionImpossible(f"{facture} : au plus {reste} FCFA restent à payer.")
    if montant > disponible(paiement):
        raise ActionImpossible("Le montant affecté dépasse le paiement.")
    return AffectationPaiement.objects.create(paiement=paiement, facture=facture, montant=montant)


@transaction.atomic
def enregistrer_paiement(
    *, client, montant, date=None, mode, reference="", justificatif=None, affectations=None
):
    """Paiement d'un client et, si demandé, son affectation à ses factures (§15).

    `affectations` : {facture: montant}. Le reste éventuel demeure en avance (8.5).
    """
    _exiger("paiements.enregistrer_paiement")
    entreprise_obligatoire()
    client = Client.objects.get(pk=client.pk)
    montant = _montant(montant)
    if montant <= 0:
        raise ActionImpossible("Indiquer le montant reçu.")
    if mode not in ModePaiement.values:
        raise ActionImpossible("Choisir le mode de paiement.")
    date = date or timezone.localdate()
    if date > timezone.localdate():
        raise ActionImpossible("La date du paiement ne peut pas être dans le futur.")
    if justificatif is not None:
        type_mime = getattr(justificatif, "content_type", "") or ""
        if type_mime not in TYPES_JUSTIFICATIF and not type_mime.startswith("image/"):
            raise ActionImpossible("Joindre le justificatif en PDF ou en image.")
    paiement = Paiement.objects.create(
        client=client, montant=montant, date=date, mode=mode, reference=(reference or "").strip()
    )
    for facture, part in (affectations or {}).items():
        if _montant(part) != 0:  # 0 : facture laissée de côté dans le formulaire
            _affecter(paiement, facture, part)
    if justificatif is not None:
        paiement.justificatif = joindre_fichier(
            paiement, justificatif, PieceJointe.Type.JUSTIFICATIF
        )
        paiement.save()
    return paiement


@transaction.atomic
def affecter_paiement(paiement, facture, montant):
    """Affecte une avance (part non affectée d'un paiement) à une facture (§7)."""
    paiement = Paiement.objects.select_for_update().get(pk=paiement.pk)
    _exiger("paiements.affecter_paiement", paiement)
    if paiement.annule:
        raise ActionImpossible("Paiement annulé.")
    return _affecter(paiement, facture, montant)


@transaction.atomic
def annuler_paiement(paiement, motif):
    """Comptabilité ou Direction, avec motif : ses affectations ne comptent plus, les
    soldes des factures sont rétablis (8.5)."""
    paiement = Paiement.objects.select_for_update().get(pk=paiement.pk)
    _exiger("paiements.annuler_paiement", paiement)
    if paiement.annule:
        raise ActionImpossible("Paiement déjà annulé.")
    if not (motif or "").strip():
        raise ActionImpossible("Indiquer le motif de l'annulation.")
    paiement.annule = True
    paiement.motif_annulation = motif.strip()
    paiement.annule_par = _utilisateur()
    paiement.annule_le = timezone.now()
    paiement.save()
    return paiement
