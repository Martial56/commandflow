"""Permissions des clients.

Lecture : les rôles qui ont l'écran Clients (grille §3.1).
Création et modification : Direction et Gestionnaire. Le §5 du cahier des charges
limite le Commercial à « consulter les clients ».
"""

import rules

from comptes.models import Role
from comptes.rules import a_le_role, meme_entreprise

voir_clients = a_le_role(
    Role.DIRECTION, Role.GESTIONNAIRE, Role.COMMERCIAL, Role.FACTURATION, Role.COMPTABILITE
)
gerer_clients = a_le_role(Role.DIRECTION, Role.GESTIONNAIRE)

rules.add_perm("clients.voir_client", voir_clients)
rules.add_perm("clients.gerer_client", gerer_clients)
rules.add_perm("clients.gerer_ce_client", gerer_clients & meme_entreprise)
