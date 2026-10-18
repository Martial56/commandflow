"""Permissions du catalogue (décision 8.1 : gestion par la Direction et le Gestionnaire)."""

import rules

from comptes.models import Role
from comptes.rules import a_le_role

gerer_catalogue = a_le_role(Role.DIRECTION, Role.GESTIONNAIRE)

# Le nom de l'app sert à l'administration Django (accès au module).
rules.add_perm("catalogue", gerer_catalogue)
rules.add_perm("catalogue.view_produit", gerer_catalogue)
rules.add_perm("catalogue.add_produit", gerer_catalogue)
rules.add_perm("catalogue.change_produit", gerer_catalogue)
# Pas de « delete_produit » : un produit se désactive.
