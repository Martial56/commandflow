"""Permissions de la facturation (grille §3.1, décisions 8.5)."""

import rules

from comptes.models import Role
from comptes.rules import a_le_role, meme_entreprise

# Consulter les documents : rôles qui ont l'écran Facturation (grille §3.1).
rules.add_perm(
    "facturation.voir_factures",
    a_le_role(Role.DIRECTION, Role.GESTIONNAIRE, Role.FACTURATION, Role.COMPTABILITE),
)
# Choisir FNE ou autre document, joindre le PDF : Facturation, Gestionnaire, Direction (8.5).
rules.add_perm(
    "facturation.emettre_facture",
    a_le_role(Role.DIRECTION, Role.GESTIONNAIRE, Role.FACTURATION),
)
rules.add_perm(
    "facturation.completer_facture",
    a_le_role(Role.DIRECTION, Role.GESTIONNAIRE, Role.FACTURATION) & meme_entreprise,
)
# Annuler un document : Comptabilité, Direction, avec motif (8.5).
rules.add_perm(
    "facturation.annuler_facture",
    a_le_role(Role.DIRECTION, Role.COMPTABILITE) & meme_entreprise,
)
# Marquer « contesté / à vérifier » : Comptabilité (8.5).
rules.add_perm("facturation.marquer_a_verifier", a_le_role(Role.COMPTABILITE) & meme_entreprise)
