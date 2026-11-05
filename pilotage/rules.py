"""Contenu du tableau de bord selon le rôle (grille §3.1, décision 8.5)."""

import rules

from comptes.models import Role
from comptes.rules import a_le_role

responsable = a_le_role(Role.DIRECTION, Role.GESTIONNAIRE)

# Tournées, manques de production, demandes à décider : Direction, Gestionnaire.
rules.add_perm("pilotage.voir_suivi", responsable)
# Créances : Direction, Gestionnaire, Comptabilité (le Commercial n'en voit pas, 8.5).
rules.add_perm(
    "pilotage.voir_creances", a_le_role(Role.DIRECTION, Role.GESTIONNAIRE, Role.COMPTABILITE)
)
# Commandes de tous : tous sauf le Commercial seul, limité à ses commandes (8.5).
rules.add_perm(
    "pilotage.voir_toutes_commandes",
    responsable | a_le_role(Role.COMPTABILITE),
)
