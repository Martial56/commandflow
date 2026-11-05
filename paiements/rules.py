"""Permissions des paiements (grille §3.1, décisions 8.5)."""

import rules

from comptes.models import Role
from comptes.rules import a_le_role, meme_entreprise

# Consulter paiements et créances : rôles qui ont l'écran Paiements (grille §3.1).
rules.add_perm(
    "paiements.voir_paiements", a_le_role(Role.DIRECTION, Role.GESTIONNAIRE, Role.COMPTABILITE)
)
# Enregistrer et affecter un paiement : Comptabilité, Gestionnaire, Direction (8.5).
rules.add_perm(
    "paiements.enregistrer_paiement",
    a_le_role(Role.DIRECTION, Role.GESTIONNAIRE, Role.COMPTABILITE),
)
rules.add_perm(
    "paiements.affecter_paiement",
    a_le_role(Role.DIRECTION, Role.GESTIONNAIRE, Role.COMPTABILITE) & meme_entreprise,
)
# Annuler un paiement : Comptabilité, Direction, avec motif (8.5).
rules.add_perm(
    "paiements.annuler_paiement", a_le_role(Role.DIRECTION, Role.COMPTABILITE) & meme_entreprise
)
