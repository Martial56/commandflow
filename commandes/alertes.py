"""Pastille du menu Commandes (§16.1, décision 8.6) : commandes à confirmer.

Pas de notification à chaque nouvelle commande (50 à 150 par jour) : la pastille suffit.
Elle concerne ceux qui confirment les commandes (Direction, Gestionnaire, Commercial).
"""

from comptes.models import Role
from comptes.rules import a_le_role
from noyau.contexte import entreprise_courante

from .models import Commande, Statut

confirme_les_commandes = a_le_role(Role.DIRECTION, Role.GESTIONNAIRE, Role.COMMERCIAL)


def nombre_alertes(utilisateur):
    # Pas d'entreprise courante (code de vérification à activer) : pas de pastille.
    if entreprise_courante() is None or not confirme_les_commandes.test(utilisateur):
        return 0
    return Commande.objects.filter(statut=Statut.A_CONFIRMER).count()
