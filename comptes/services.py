"""Gestion des comptes depuis l'écran Paramètres (maquette 17, décisions 8.6).

Pas d'envoi par e-mail ni par SMS : un responsable crée le compte ou réinitialise le mot
de passe, l'application affiche une seule fois un mot de passe provisoire, que la personne
doit changer à sa connexion. Aucun compte n'est supprimé : il est désactivé.
"""

import secrets

from allauth.mfa.models import Authenticator
from axes.utils import reset as debloquer_axes
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.db import transaction

from noyau.contexte import entreprise_obligatoire, utilisateur_courant

from .adaptateurs import normaliser_telephone
from .models import ROLES_DOUBLE_AUTHENTIFICATION, Role, Utilisateur, valider_telephone
from .rules import est_direction

# Lettres et chiffres sans ambiguïté à la lecture (ni 0/O, ni 1/l/I).
ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
# Les prestataires ont un compte lié à leur fiche : il se crée dans l'administration.
ROLES_CREABLES = [role for role in Role if role != Role.PRESTATAIRE]


class ActionImpossible(Exception):
    """Action refusée pour une raison métier ; le message est affiché tel quel."""


def mot_de_passe_provisoire():
    """« Kp7m-x3Qa-9rTe » : trois groupes de quatre caractères faciles à dicter."""
    return "-".join("".join(secrets.choice(ALPHABET) for _ in range(4)) for _ in range(3))


def _responsable():
    utilisateur = utilisateur_courant()
    if utilisateur is None or not utilisateur.has_perm("comptes.gerer_comptes"):
        raise PermissionDenied
    return utilisateur


def _exiger_sur(cible):
    responsable = _responsable()
    if not responsable.has_perm("comptes.gerer_compte", cible):
        raise PermissionDenied
    return responsable


@transaction.atomic
def creer_compte(*, prenom, nom, telephone, role, email=""):
    """Nouveau compte de l'entreprise courante ; renvoie (compte, mot de passe provisoire)."""
    responsable = _responsable()
    entreprise = entreprise_obligatoire()
    if role not in ROLES_CREABLES:
        raise ActionImpossible("Choisir un rôle.")
    if role in ROLES_DOUBLE_AUTHENTIFICATION and not est_direction(responsable):
        raise ActionImpossible(
            "Seule la Direction crée un compte Direction ou Comptabilité (code de vérification)."
        )
    if not (prenom or "").strip() or not (nom or "").strip():
        raise ActionImpossible("Indiquer le prénom et le nom.")
    telephone = normaliser_telephone(telephone)
    try:
        valider_telephone(telephone)
    except Exception as erreur:
        raise ActionImpossible(
            "Téléphone à 10 chiffres (07 00 00 00 00) ou au format international (+…)."
        ) from erreur
    if Utilisateur.objects.filter(telephone=telephone).exists():
        raise ActionImpossible("Ce numéro de téléphone a déjà un compte.")
    email = (email or "").strip().lower()
    if email and Utilisateur.objects.filter(email__iexact=email).exists():
        raise ActionImpossible("Cette adresse e-mail a déjà un compte.")
    provisoire = mot_de_passe_provisoire()
    compte = Utilisateur(
        username=telephone,
        first_name=prenom.strip(),
        last_name=nom.strip(),
        telephone=telephone,
        email=email,
        entreprise=entreprise,
        role_principal=role,
        doit_changer_mot_de_passe=True,
    )
    compte.set_password(provisoire)
    compte.save()
    compte.groups.add(Group.objects.get(name=Role(role).label))
    return compte, provisoire


@transaction.atomic
def reinitialiser_mot_de_passe(cible):
    """Mot de passe provisoire (B1) ; renvoie ce mot de passe, affiché une seule fois.

    Le blocage après échecs est levé. Par la Direction, le code de vérification est aussi
    effacé : la personne le réactive à sa prochaine connexion (téléphone perdu, 8.6)."""
    responsable = _exiger_sur(cible)
    cible = Utilisateur.objects.select_for_update().get(pk=cible.pk)
    provisoire = mot_de_passe_provisoire()
    cible.set_password(provisoire)
    cible.doit_changer_mot_de_passe = True
    cible.save()
    debloquer_axes(username=cible.telephone)
    if est_direction(responsable):
        Authenticator.objects.filter(user=cible).delete()
    return provisoire


@transaction.atomic
def desactiver_compte(cible):
    _exiger_sur(cible)
    cible = Utilisateur.objects.select_for_update().get(pk=cible.pk)
    if not cible.is_active:
        raise ActionImpossible("Ce compte est déjà désactivé.")
    cible.is_active = False
    cible.save()
    return cible


@transaction.atomic
def reactiver_compte(cible):
    _exiger_sur(cible)
    cible = Utilisateur.objects.select_for_update().get(pk=cible.pk)
    if cible.is_active:
        raise ActionImpossible("Ce compte est déjà actif.")
    cible.is_active = True
    cible.save()
    return cible
