"""Menus et écrans visibles selon le rôle (grille validée le 2026-10-18).

Chaque écran déclare une permission django-rules `ecrans.<code>`. Elle sert à la fois
à afficher le menu et à refuser l'accès direct par URL (403) : le contrôle est fait
côté serveur, jamais seulement dans le gabarit.

Écrans de bureau : barre de menus (maquettes 8 à 17).
Écrans de terrain : pas de menu, le profil arrive directement sur son écran (maquettes 2 à 6).
"""

from dataclasses import dataclass

import rules

from .models import Role
from .rules import a_le_role, peut_gerer_utilisateurs


@dataclass(frozen=True)
class Ecran:
    code: str
    libelle: str
    chemin: str
    acces: rules.Predicate
    terrain: bool = False
    sprint: int | None = None

    @property
    def permission(self):
        return f"ecrans.{self.code}"

    @property
    def nom_url(self):
        return f"ecran_{self.code}"


R = Role
ECRANS = [
    Ecran(
        "tableau_de_bord",
        "Tableau de bord",
        "tableau-de-bord/",
        # Commercial : tableau de bord limité à ses propres commandes (contenu au sprint 6).
        a_le_role(R.DIRECTION, R.GESTIONNAIRE, R.COMMERCIAL, R.COMPTABILITE),
        sprint=6,
    ),
    Ecran(
        "commandes",
        "Commandes",
        "commandes/",
        a_le_role(R.DIRECTION, R.GESTIONNAIRE, R.COMMERCIAL, R.FACTURATION),
        sprint=2,
    ),
    Ecran(
        "recurrentes",
        "Récurrentes",
        "recurrentes/",
        a_le_role(R.DIRECTION, R.GESTIONNAIRE, R.COMMERCIAL),
        sprint=2,
    ),
    Ecran(
        "clients",
        "Clients",
        "clients/",
        a_le_role(R.DIRECTION, R.GESTIONNAIRE, R.COMMERCIAL, R.FACTURATION, R.COMPTABILITE),
        sprint=2,
    ),
    Ecran(
        "production",
        "Production",
        "production/",
        # Responsable de production : plan groupé uniquement.
        a_le_role(R.DIRECTION, R.GESTIONNAIRE, R.PRODUCTION),
        sprint=3,
    ),
    Ecran(
        "tournees",
        "Tournées",
        "tournees/",
        a_le_role(R.DIRECTION, R.GESTIONNAIRE),
        sprint=4,
    ),
    Ecran(
        "facturation",
        "Facturation",
        "facturation/",
        a_le_role(R.DIRECTION, R.GESTIONNAIRE, R.FACTURATION, R.COMPTABILITE),
        sprint=6,
    ),
    Ecran(
        "paiements",
        "Paiements",
        "paiements/",
        a_le_role(R.DIRECTION, R.GESTIONNAIRE, R.COMPTABILITE),
        sprint=6,
    ),
    Ecran(
        "parametres",
        "Paramètres",
        "parametres/",
        # La gestion des utilisateurs est une permission individuelle, quel que soit le rôle.
        a_le_role(R.DIRECTION, R.GESTIONNAIRE) | peut_gerer_utilisateurs,
    ),
    Ecran(
        "chargement",
        "Contrôle du chargement",
        "chargement/",
        a_le_role(R.PREPARATION),
        terrain=True,
        sprint=4,
    ),
    Ecran(
        "ma_tournee",
        "Ma tournée",
        "ma-tournee/",
        a_le_role(R.CHAUFFEUR, R.PRESTATAIRE),
        terrain=True,
        sprint=5,
    ),
]
ECRANS_PAR_CODE = {ecran.code: ecran for ecran in ECRANS}

for _ecran in ECRANS:
    rules.add_perm(_ecran.permission, _ecran.acces)

# Écran d'arrivée après connexion, selon le rôle principal.
ARRIVEE_PAR_ROLE = {
    R.DIRECTION: "tableau_de_bord",
    R.GESTIONNAIRE: "tableau_de_bord",
    R.COMMERCIAL: "tableau_de_bord",
    R.PRODUCTION: "production",
    R.PREPARATION: "chargement",
    R.CHAUFFEUR: "ma_tournee",
    R.PRESTATAIRE: "ma_tournee",
    R.FACTURATION: "facturation",
    R.COMPTABILITE: "paiements",
}


def ecrans_autorises(utilisateur):
    return [ecran for ecran in ECRANS if utilisateur.has_perm(ecran.permission)]


def menus(utilisateur):
    """Entrées de la barre de menus (écrans de bureau autorisés, dans l'ordre des maquettes)."""
    return [ecran for ecran in ecrans_autorises(utilisateur) if not ecran.terrain]


def ecran_arrivee(utilisateur):
    """Écran du rôle principal s'il est autorisé, sinon le premier écran autorisé."""
    code = ARRIVEE_PAR_ROLE.get(utilisateur.role_principal)
    if code and utilisateur.has_perm(ECRANS_PAR_CODE[code].permission):
        return ECRANS_PAR_CODE[code]
    autorises = ecrans_autorises(utilisateur)
    return autorises[0] if autorises else None
