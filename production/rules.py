"""Permissions de la production (décisions 8.2)."""

import rules

from comptes.models import Role
from comptes.rules import a_le_role, meme_entreprise

# Responsables du plan : valider, ajuster « à produire », clôturer.
responsable_production = a_le_role(Role.PRODUCTION, Role.GESTIONNAIRE, Role.DIRECTION)

rules.add_perm("production.voir_plan", responsable_production)
rules.add_perm("production.gerer_plan", responsable_production & meme_entreprise)
# Saisie des fournées : Production, Gestionnaire.
atelier = a_le_role(Role.PRODUCTION, Role.GESTIONNAIRE)
rules.add_perm("production.acceder_saisie", atelier)  # écran de saisie (maquette 2)
rules.add_perm("production.saisir_production", atelier & meme_entreprise)
