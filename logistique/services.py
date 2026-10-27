"""Services de la logistique : toute écriture des tournées et des chargements passe ici.

Les vues appellent ces services ; les services vérifient les droits de l'utilisateur
courant et appellent les transitions (§5 de modele-donnees.md, décisions 8.3). La tournée
est verrouillée (`select_for_update`) pendant chaque modification de ses chargements.
"""

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Max
from django_fsm import can_proceed

from commandes.models import Commande, Statut
from comptes.models import Role
from entreprises.parametres import creneaux
from noyau.contexte import AutreEntreprise, entreprise_obligatoire, utilisateur_courant
from noyau.services import prochain_numero

from .models import (
    Chargement,
    LigneChargement,
    StatutChargement,
    StatutTournee,
    Tournee,
)

# Tournée modifiable : pas encore partie (décision 8.3 : une tournée partie est figée).
STATUTS_MODIFIABLES = [
    StatutTournee.PLANIFIEE,
    StatutTournee.EN_CHARGEMENT,
    StatutTournee.DEPART_AUTORISE,
]
# Chargement encore dans sa tournée (tout sauf « Retiré »).
STATUTS_CHARGEMENT_ACTIFS = [s for s in StatutChargement.values if s != StatutChargement.RETIRE]


class ActionImpossible(Exception):
    """Action refusée par une règle métier ; le message s'affiche tel quel."""


# --- Outils -------------------------------------------------------------------


def _utilisateur():
    utilisateur = utilisateur_courant()
    if utilisateur is None:
        raise PermissionDenied("Action à attribuer à un utilisateur connecté.")
    return utilisateur


def _exiger(permission, objet=None):
    if not _utilisateur().has_perm(permission, objet):
        raise PermissionDenied("Votre rôle ne permet pas cette action.")


def _meme_entreprise(*objets):
    entreprise = entreprise_obligatoire()
    for objet in objets:
        if objet is not None and objet.entreprise_id != entreprise.pk:
            raise AutreEntreprise(f"{objet} n'appartient pas à l'entreprise courante.")


def _verrouiller(tournee):
    return Tournee.objects.select_for_update().get(pk=tournee.pk)


def _exiger_modifiable(tournee):
    if tournee.statut not in STATUTS_MODIFIABLES:
        raise ActionImpossible("Tournée partie : elle ne peut plus être modifiée.")


def _verifier_livreur(chauffeur, prestataire, vehicule):
    """Un chauffeur interne ou un prestataire, actif et de l'entreprise (§3.5)."""
    if (chauffeur is None) == (prestataire is None):
        raise ActionImpossible("Choisir un chauffeur ou un prestataire.")
    _meme_entreprise(chauffeur, prestataire, vehicule)
    if chauffeur is not None and not (
        chauffeur.is_active and chauffeur.groups.filter(name=Role.CHAUFFEUR.label).exists()
    ):
        raise ActionImpossible(f"{chauffeur} n'est pas un chauffeur actif.")
    if prestataire is not None and not prestataire.actif:
        raise ActionImpossible(f"Le prestataire {prestataire} est désactivé.")
    if vehicule is not None and not vehicule.actif:
        raise ActionImpossible(f"Le véhicule {vehicule} est désactivé.")


def _numeroter(tournee, chargements):
    """Ordre de passage 1, 2, 3… dans l'ordre donné."""
    for ordre, chargement in enumerate(chargements, start=1):
        if chargement.ordre_passage != ordre:
            chargement.ordre_passage = ordre
            chargement.save()


def actualiser_tournee(tournee):
    """Statut de la tournée selon ses chargements (actions du système, §5).

    Départ autorisé dès que chaque chargement actif peut partir ; retour « en
    chargement » sinon (commande ajoutée, réception contestée).
    """
    if tournee.statut == StatutTournee.EN_CHARGEMENT and can_proceed(tournee.autoriser_depart):
        tournee.autoriser_depart()
        tournee.save()
    elif tournee.statut == StatutTournee.DEPART_AUTORISE and not tournee.tout_peut_partir():
        tournee.rouvrir_chargement()
        tournee.save()
    return tournee


# --- Tournées -----------------------------------------------------------------


@transaction.atomic
def creer_tournee(*, date, chauffeur=None, prestataire=None, vehicule=None):
    """Nouvelle tournée planifiée, numérotée `TRN-AAAA-000123` (Gestionnaire, Direction)."""
    _exiger("logistique.gerer_tournees")
    _verifier_livreur(chauffeur, prestataire, vehicule)
    return Tournee.objects.create(
        numero=prochain_numero("TRN", date_reference=date),
        date=date,
        chauffeur=chauffeur,
        prestataire=prestataire,
        vehicule=vehicule,
    )


@transaction.atomic
def changer_livreur(tournee, *, chauffeur=None, prestataire=None, vehicule=None):
    """Change le chauffeur (ou le prestataire) et le véhicule avant le départ.

    Refusé si un chargement est déjà reçu : la réception engage le livreur qui l'a
    confirmée ; retirer puis réaffecter la commande.
    """
    tournee = _verrouiller(tournee)
    _exiger("logistique.gerer_tournee", tournee)
    _exiger_modifiable(tournee)
    _verifier_livreur(chauffeur, prestataire, vehicule)
    if tournee.chargements.filter(statut=StatutChargement.RECUE_CHAUFFEUR).exists():
        raise ActionImpossible(
            "Des commandes sont déjà reçues par le livreur : les retirer avant de le changer."
        )
    tournee.chauffeur = chauffeur
    tournee.prestataire = prestataire
    tournee.vehicule = vehicule
    tournee.save()
    return tournee


# --- Affectation des commandes -------------------------------------------------


def commandes_affectables(date):
    """Commandes « Prête au chargement » de la date, sans chargement actif (8.3)."""
    return (
        Commande.objects.filter(statut=Statut.PRETE_CHARGEMENT, date_livraison=date)
        .exclude(chargements__statut__in=STATUTS_CHARGEMENT_ACTIFS)
        .select_related("client", "adresse")
        .order_by("creneau", "adresse__commune", "numero")
    )


@transaction.atomic
def affecter_commande(tournee, commande):
    """Ajoute une commande à la tournée : un chargement, une ligne par produit."""
    tournee = _verrouiller(tournee)
    _exiger("logistique.gerer_tournee", tournee)
    _exiger_modifiable(tournee)
    commande = Commande.objects.select_for_update().get(pk=commande.pk)
    if commande.statut != Statut.PRETE_CHARGEMENT:
        raise ActionImpossible(
            f"{commande} n'est pas prête au chargement ({commande.get_statut_display()})."
        )
    if commande.date_livraison != tournee.date:
        raise ActionImpossible(f"{commande} n'est pas à livrer le jour de la tournée.")
    if commande.chargements.filter(statut__in=STATUTS_CHARGEMENT_ACTIFS).exists():
        raise ActionImpossible(f"{commande} est déjà affectée à une tournée.")

    dernier = tournee.chargements_actifs().aggregate(Max("ordre_passage"))["ordre_passage__max"]
    chargement = Chargement.objects.create(
        tournee=tournee, commande=commande, ordre_passage=(dernier or 0) + 1
    )
    for ligne in commande.lignes_actives.order_by("pk"):
        LigneChargement.objects.create(
            chargement=chargement, ligne_commande=ligne, quantite_attendue=ligne.quantite
        )
    actualiser_tournee(tournee)
    return chargement


@transaction.atomic
def retirer_commande(chargement):
    """Retire une commande avant le départ ; elle redevient affectable (8.3)."""
    tournee = _verrouiller(chargement.tournee)
    chargement = Chargement.objects.select_for_update().get(pk=chargement.pk)
    if not can_proceed(chargement.retirer, check_conditions=False):
        raise ActionImpossible("Commande déjà retirée de la tournée.")
    if not chargement.retirer._django_fsm.has_transition_perm(
        chargement, chargement.statut, _utilisateur()
    ):
        raise PermissionDenied("Votre rôle ne permet pas cette action.")
    if not can_proceed(chargement.retirer):
        raise ActionImpossible("Tournée partie : elle ne peut plus être modifiée.")
    chargement.retirer()
    chargement.save()
    _numeroter(tournee, tournee.chargements_actifs().order_by("ordre_passage", "pk"))
    actualiser_tournee(tournee)
    return chargement


# --- Ordre de passage ----------------------------------------------------------------


@transaction.atomic
def reordonner(tournee, chargements):
    """Fixe l'ordre de passage : `chargements` contient tous les chargements actifs."""
    tournee = _verrouiller(tournee)
    _exiger("logistique.gerer_tournee", tournee)
    _exiger_modifiable(tournee)
    actifs = {chargement.pk: chargement for chargement in tournee.chargements_actifs()}
    ordre = [getattr(chargement, "pk", chargement) for chargement in chargements]
    if sorted(ordre) != sorted(actifs):
        raise ActionImpossible("L'ordre doit reprendre toutes les commandes de la tournée.")
    _numeroter(tournee, [actifs[pk] for pk in ordre])
    return tournee


@transaction.atomic
def proposer_ordre(tournee):
    """Ordre proposé : par créneau, puis par commune (8.3) ; le gestionnaire ajuste."""
    tournee = _verrouiller(tournee)
    _exiger("logistique.gerer_tournee", tournee)
    _exiger_modifiable(tournee)
    rangs = {creneau: rang for rang, creneau in enumerate(creneaux(tournee.entreprise))}
    chargements = sorted(
        tournee.chargements_actifs().select_related("commande__adresse"),
        key=lambda c: (
            rangs.get(c.commande.creneau, len(rangs)),
            c.commande.creneau,
            c.commande.adresse.commune.casefold(),
            c.commande.numero,
        ),
    )
    _numeroter(tournee, chargements)
    return chargements
