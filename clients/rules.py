"""Permissions des clients.

Lecture : les rôles qui ont l'écran Clients (grille §3.1).
Création : Direction, Gestionnaire et Commercial (décision 8.1 du 19 octobre 2026).
Ajout d'une adresse à un client existant : Direction, Gestionnaire et Commercial
(décision 8.1) ; choix de l'adresse par défaut et retrait : Direction et Gestionnaire.
Modification et désactivation : Direction et Gestionnaire.
"""

import rules

from comptes.models import Role
from comptes.rules import a_le_role, meme_entreprise

voir_clients = a_le_role(
    Role.DIRECTION, Role.GESTIONNAIRE, Role.COMMERCIAL, Role.FACTURATION, Role.COMPTABILITE
)
creer_clients = a_le_role(Role.DIRECTION, Role.GESTIONNAIRE, Role.COMMERCIAL)
gerer_clients = a_le_role(Role.DIRECTION, Role.GESTIONNAIRE)

rules.add_perm("clients.voir_client", voir_clients)
rules.add_perm("clients.creer_client", creer_clients)
rules.add_perm("clients.gerer_client", gerer_clients)
rules.add_perm("clients.gerer_ce_client", gerer_clients & meme_entreprise)
rules.add_perm("clients.ajouter_adresse", creer_clients & meme_entreprise)
