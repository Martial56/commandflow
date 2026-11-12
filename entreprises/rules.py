"""Permissions des réglages de l'entreprise (écran Paramètres, décision 8.6)."""

import rules

from comptes.models import Role
from comptes.rules import a_le_role

# Preuves exigées, heures et créneaux : Direction et Gestionnaire.
rules.add_perm("entreprises.modifier_parametres", a_le_role(Role.DIRECTION, Role.GESTIONNAIRE))
