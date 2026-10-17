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
