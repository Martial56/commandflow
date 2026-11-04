"""Services de facturation (§7 `enregistrer_choix`, §14 ; décisions 8.5).

Toute écriture d'un document passe ici. Le choix FNE ou autre document crée une `Facture`
liée aux livraisons ; il ne change jamais le statut d'une livraison ni d'une commande.
"""

import calendar
import datetime
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone
from django_fsm import can_proceed

from clients.models import ConditionsPaiement
from commandes.models import arrondir_fcfa
from logistique.models import LigneLivraison, Livraison, StatutLivraison
from noyau.contexte import utilisateur_courant
from noyau.models import PieceJointe
from noyau.services import joindre_fichier, prochain_numero

from .models import Facture, FactureLivraison, NatureAutreDocument, StatutFacture, TypeDocument

# Livraisons facturables : une partie au moins a été remise au client.
STATUTS_LIVRAISON_FACTURABLE = [StatutLivraison.LIVREE, StatutLivraison.PARTIELLEMENT_LIVREE]
TYPES_DOCUMENT_ACCEPTES = {"application/pdf"}


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


# --- Lectures ------------------------------------------------------------------------


def livraisons_a_facturer():
    """Livraisons livrées ou partielles qu'aucun document émis ne couvre encore."""
    return (
        Livraison.objects.filter(statut__in=STATUTS_LIVRAISON_FACTURABLE)
        .exclude(lignes_facture__facture__statut=StatutFacture.EMISE)
        .select_related("commande__client", "chargement__tournee")
        .order_by("commande__client__nom", "traitee_le", "pk")
    )


def montants_livraison(livraison):
    """(HT, TTC) d'une livraison : quantités livrées × prix figé de la ligne de commande
    (TTC, décision 8.5), arrondis au franc par ligne ; HT = TTC ÷ (1 + taux du produit)."""
    total_ht = total_ttc = Decimal(0)
    for ligne in LigneLivraison.objects.filter(livraison=livraison).select_related(
        "ligne_commande__produit"
    ):
        if not ligne.quantite_livree:
            continue
        ttc = arrondir_fcfa(ligne.quantite_livree * ligne.ligne_commande.prix_unitaire)
        taux = ligne.ligne_commande.produit.taux_tva
        total_ttc += ttc
        total_ht += arrondir_fcfa(ttc / (1 + taux / 100)) if taux else ttc
    return total_ht, total_ttc


def echeance_par_defaut(client, date_document):
    """Échéance selon les conditions du client (décision 8.5) : comptant, fin de mois,
    ou date + X jours."""
    if client.conditions_paiement == ConditionsPaiement.FIN_DE_MOIS:
        dernier_jour = calendar.monthrange(date_document.year, date_document.month)[1]
        return date_document.replace(day=dernier_jour)
    if client.conditions_paiement == ConditionsPaiement.X_JOURS:
        return date_document + datetime.timedelta(days=client.delai_paiement_jours or 0)
    return date_document


# --- Écritures -----------------------------------------------------------------------


def _verifier_document(fichier):
    type_mime = getattr(fichier, "content_type", "") or ""
    if type_mime not in TYPES_DOCUMENT_ACCEPTES and not type_mime.startswith("image/"):
        raise ActionImpossible("Joindre le document en PDF (ou une image scannée).")


@transaction.atomic
def enregistrer_choix(
    livraisons,
    *,
    type_document,
    numero_fne="",
    nature="",
    motif="",
    fichier=None,
    date_document=None,
    echeance=None,
):
    """Choix FNE ou autre document pour des livraisons d'un même client (§14, 8.5).

    Crée la `Facture` (numéro FAC, montants figés, échéance selon le client) et la relie
    aux livraisons, avec qui a fait le choix et quand. Aucun statut de livraison ni de
    commande ne change (§22-22).
    """
    _exiger("facturation.emettre_facture")
    pks = sorted({getattr(livraison, "pk", livraison) for livraison in livraisons})
    if not pks:
        raise ActionImpossible("Sélectionner au moins une livraison.")
    choisies = list(
        Livraison.objects.select_for_update()
        .filter(pk__in=pks)
        .select_related("commande__client")
        .order_by("pk")
    )
    if len(choisies) != len(pks):
        raise ActionImpossible("Livraison introuvable.")
    clients = {livraison.commande.client_id for livraison in choisies}
    if len(clients) > 1:
        raise ActionImpossible("Un document ne couvre que les livraisons d'un même client.")
    facturables = set(livraisons_a_facturer().filter(pk__in=pks).values_list("pk", flat=True))
    deja = [livraison for livraison in choisies if livraison.pk not in facturables]
    if deja:
        raise ActionImpossible(
            f"{deja[0].commande} : livraison déjà facturée ou pas encore livrée."
        )

    numero_fne, nature, motif = (numero_fne or "").strip(), nature or "", (motif or "").strip()
    if type_document == TypeDocument.FNE:
        if not numero_fne:
            raise ActionImpossible("Indiquer le n° de la FNE émise sur la plateforme de la DGI.")
        if Facture.objects.filter(numero_fne=numero_fne, statut=StatutFacture.EMISE).exists():
            raise ActionImpossible(f"La FNE {numero_fne} est déjà enregistrée.")
        nature, motif = "", ""
    elif type_document == TypeDocument.AUTRE:
        if nature not in NatureAutreDocument.values:
            raise ActionImpossible("Choisir le type de document.")
        if not motif:
            raise ActionImpossible("Indiquer le motif du traitement sans FNE.")
        numero_fne = ""
    else:
        raise ActionImpossible("Choisir « Facture normalisée (FNE) » ou « Autre document ».")
    if fichier:
        _verifier_document(fichier)

    client = choisies[0].commande.client
    date_document = date_document or timezone.localdate()
    montants = {livraison.pk: montants_livraison(livraison) for livraison in choisies}
    facture = Facture.objects.create(
        numero_interne=prochain_numero("FAC", date_reference=date_document),
        client=client,
        type_document=type_document,
        nature=nature,
        motif_sans_fne=motif,
        numero_fne=numero_fne,
        date_document=date_document,
        echeance=echeance or echeance_par_defaut(client, date_document),
        montant_ht=sum(ht for ht, _ttc in montants.values()),
        montant_ttc=sum(ttc for _ht, ttc in montants.values()),
        choix_fait_par=_utilisateur(),
        choix_fait_le=timezone.now(),
    )
    for livraison in choisies:
        ht, ttc = montants[livraison.pk]
        FactureLivraison.objects.create(
            facture=facture, livraison=livraison, montant_ht=ht, montant_ttc=ttc
        )
    if fichier:
        facture.piece = joindre_fichier(facture, fichier, PieceJointe.Type.JUSTIFICATIF)
        facture.save()
    return facture


def _verrouiller(facture):
    return Facture.objects.select_for_update().get(pk=facture.pk)


@transaction.atomic
def joindre_document(facture, fichier):
    """PDF de la FNE (ou du document) joint après coup ; l'ancien reste dans l'historique."""
    facture = _verrouiller(facture)
    _exiger("facturation.completer_facture", facture)
    if facture.statut != StatutFacture.EMISE:
        raise ActionImpossible("Document annulé : il ne peut plus être complété.")
    if not fichier:
        raise ActionImpossible("Choisir le fichier à joindre.")
    _verifier_document(fichier)
    facture.piece = joindre_fichier(facture, fichier, PieceJointe.Type.JUSTIFICATIF)
    facture.save()
    return facture


@transaction.atomic
def annuler_facture(facture, motif):
    """Annulation motivée (Comptabilité, Direction) : les livraisons redeviennent à
    facturer. Pour une FNE, la correction sur le portail de la DGI reste à faire (8.5)."""
    facture = _verrouiller(facture)
    if not can_proceed(facture.annuler, check_conditions=False):
        raise ActionImpossible("Document déjà annulé.")
    if not facture.annuler._django_fsm.has_transition_perm(facture, facture.statut, _utilisateur()):
        raise PermissionDenied("Votre rôle ne permet pas cette action.")
    facture.motif_annulation = (motif or "").strip()
    if not can_proceed(facture.annuler):
        raise ActionImpossible("Indiquer le motif de l'annulation.")
    facture.annuler()
    facture.save()
    return facture


@transaction.atomic
def marquer_a_verifier(facture, motif):
    """« Contesté / à vérifier » (Comptabilité, motif) : s'ajoute au statut de paiement."""
    facture = _verrouiller(facture)
    _exiger("facturation.marquer_a_verifier", facture)
    if not (motif or "").strip():
        raise ActionImpossible("Indiquer ce qui est contesté ou à vérifier.")
    facture.a_verifier, facture.motif_verification = True, motif.strip()
    facture.save()
    return facture


@transaction.atomic
def lever_verification(facture):
    facture = _verrouiller(facture)
    _exiger("facturation.marquer_a_verifier", facture)
    facture.a_verifier, facture.motif_verification = False, ""
    facture.save()
    return facture
