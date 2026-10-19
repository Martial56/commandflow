"""Permissions des commandes (tables §4 de modele-donnees.md, décisions 8.1)."""

import rules

from comptes.models import Role
from comptes.rules import a_le_role, meme_entreprise

from .models import STATUTS_ANNULATION_SIMPLE

commercial_ou_gestionnaire = a_le_role(Role.COMMERCIAL, Role.GESTIONNAIRE)
responsable = a_le_role(Role.DIRECTION, Role.GESTIONNAIRE)


@rules.predicate
def est_createur(utilisateur, commande):
    return commande is not None and commande.cree_par_id == utilisateur.pk


@rules.predicate
def recurrente(utilisateur, commande):
    return commande is not None and commande.modele_recurrent_id is not None


@rules.predicate
def avant_confirmation(utilisateur, commande):
    return commande is not None and commande.statut in STATUTS_ANNULATION_SIMPLE


# Consulter : rôles qui ont l'écran Commandes (grille §3.1).
rules.add_perm(
    "commandes.voir_commande",
    a_le_role(Role.DIRECTION, Role.GESTIONNAIRE, Role.COMMERCIAL, Role.FACTURATION),
)
# Saisir, soumettre, confirmer : Commercial, Gestionnaire (§4).
rules.add_perm("commandes.creer_commande", commercial_ou_gestionnaire)
rules.add_perm("commandes.soumettre_commande", commercial_ou_gestionnaire & meme_entreprise)
rules.add_perm("commandes.confirmer_commande", commercial_ou_gestionnaire & meme_entreprise)
# Annuler (§4) : avant confirmation, le créateur ou le gestionnaire ;
# après confirmation, la Direction ou le Gestionnaire (avec validation acceptée).
rules.add_perm(
    "commandes.annuler_commande",
    meme_entreprise
    & (
        (
            avant_confirmation
            & (
                est_createur
                | a_le_role(Role.GESTIONNAIRE)
                # « Pas de livraison » sur une commande générée (décision 8.1).
                | (recurrente & a_le_role(Role.COMMERCIAL))
            )
        )
        | (~avant_confirmation & responsable)
    ),
)
# Décider des validations (modification, urgence, annulation) : décision 8.1.
rules.add_perm("commandes.valider_commande", responsable & meme_entreprise)

# Modèles récurrents : gérés par la Direction et le Gestionnaire (décision 8.1),
# dans l'administration en attendant un écran dédié. Pas de suppression.
rules.add_perm("commandes", responsable)
for _modele in ("modelerecurrent", "lignemodelerecurrent"):
    for _action in ("view", "add", "change"):
        rules.add_perm(f"commandes.{_action}_{_modele}", responsable)
