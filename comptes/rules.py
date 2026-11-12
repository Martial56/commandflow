"""Prédicats de permission communs (django-rules).

Chaque app déclare ses permissions dans son propre `rules.py` en combinant ces prédicats,
par exemple : `rules.add_perm("commandes.annuler", meme_entreprise & a_le_role(...))`.
"""

import rules

from .models import Role


def a_le_role(*roles):
    """Vrai si l'utilisateur appartient à au moins un des groupes correspondant aux rôles."""
    noms = sorted(Role(role).label for role in roles)

    @rules.predicate(name=f"a_le_role({', '.join(noms)})")
    def predicat(utilisateur):
        return (
            utilisateur.is_authenticated
            and utilisateur.is_active
            and utilisateur.groups.filter(name__in=noms).exists()
        )

    return predicat


@rules.predicate
def meme_entreprise(utilisateur, objet):
    """Vrai si l'objet appartient à l'entreprise de l'utilisateur."""
    entreprise_id = getattr(utilisateur, "entreprise_id", None)
    return (
        objet is not None
        and entreprise_id is not None
        and getattr(objet, "entreprise_id", None) == entreprise_id
    )


@rules.predicate
def peut_gerer_utilisateurs(utilisateur):
    """Permission attribuée individuellement (admin Django, puis écran Paramètres)."""
    return utilisateur.has_perm("comptes.gerer_utilisateurs")


# Gestion des comptes (écran Paramètres, décision 8.6) : Direction et Gestionnaire par leur
# rôle, ou un autre profil de confiance par la permission individuelle.
gere_les_comptes = a_le_role(Role.DIRECTION, Role.GESTIONNAIRE) | peut_gerer_utilisateurs
est_direction = a_le_role(Role.DIRECTION)


def compte_sensible(cible):
    """Compte avec code de vérification (Direction, Comptabilité) : géré par la Direction."""
    from .models import ROLES_DOUBLE_AUTHENTIFICATION

    noms = [Role(role).label for role in ROLES_DOUBLE_AUTHENTIFICATION]
    return cible.role_principal in ROLES_DOUBLE_AUTHENTIFICATION or (
        cible.pk is not None and cible.groups.filter(name__in=noms).exists()
    )


@rules.predicate
def compte_gerable(utilisateur, cible):
    """Un compte de la même entreprise, autre que le sien ; un compte sensible seulement
    par la Direction."""
    if cible is None or cible.pk == utilisateur.pk:
        return False
    if cible.entreprise_id is None or cible.entreprise_id != utilisateur.entreprise_id:
        return False
    return not compte_sensible(cible) or est_direction(utilisateur)


rules.add_perm("comptes.gerer_comptes", gere_les_comptes)
rules.add_perm("comptes.gerer_compte", gere_les_comptes & compte_gerable)
