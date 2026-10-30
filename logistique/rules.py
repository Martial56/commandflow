"""Permissions de la logistique (§5 de modele-donnees.md, décisions 8.3)."""

import rules

from comptes.models import Role
from comptes.rules import a_le_role, meme_entreprise

responsable = a_le_role(Role.DIRECTION, Role.GESTIONNAIRE)


def _tournee(objet):
    return getattr(objet, "tournee", objet)


@rules.predicate
def livreur_affecte(utilisateur, objet):
    """Le chauffeur de la tournée, ou le compte du prestataire : lui seul confirme (§10)."""
    if objet is None:
        return False
    compte = _tournee(objet).compte_livreur
    return compte is not None and compte.pk == utilisateur.pk


@rules.predicate
def prestataire_sans_compte(utilisateur, objet):
    tournee = _tournee(objet) if objet is not None else None
    return (
        tournee is not None
        and tournee.prestataire_id is not None
        and tournee.compte_livreur is None
    )


# Créer les tournées, affecter, retirer, réordonner : Gestionnaire, Direction (8.3).
rules.add_perm("logistique.gerer_tournees", responsable)
rules.add_perm("logistique.gerer_tournee", responsable & meme_entreprise)
# Contrôler le chargement : Préparation, Gestionnaire (8.3). La liste des arrêts reste
# réservée à la Préparation (grille §3.1) ; le Gestionnaire ouvre un arrêt depuis Tournées.
rules.add_perm("logistique.acceder_controle", a_le_role(Role.PREPARATION, Role.GESTIONNAIRE))
rules.add_perm(
    "logistique.controler_chargement",
    a_le_role(Role.PREPARATION, Role.GESTIONNAIRE) & meme_entreprise,
)
# Recevoir le chargement et démarrer : le livreur affecté ; pour un prestataire sans
# compte, le gestionnaire à sa place (avec validation d'un autre responsable, 8.3).
rules.add_perm(
    "logistique.recevoir_chargement",
    meme_entreprise & (livreur_affecte | (prestataire_sans_compte & a_le_role(Role.GESTIONNAIRE))),
)
# Demander l'exception de départ : Gestionnaire (8.3).
rules.add_perm(
    "logistique.demander_exception_depart", a_le_role(Role.GESTIONNAIRE) & meme_entreprise
)
# Décider des exceptions de chargement et de départ : Direction, Gestionnaire.
rules.add_perm("logistique.valider_exception", responsable & meme_entreprise)
