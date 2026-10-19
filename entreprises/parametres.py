"""Paramètres réglables par entreprise, lus dans `Entreprise.options` (décisions 8.1).

Valeurs par défaut quand l'entreprise ne les a pas encore réglées (écran Paramètres
à venir).
"""

import datetime

CRENEAUX_PAR_DEFAUT = ["06h–08h", "08h–10h", "10h–12h", "16h–18h"]
HEURE_GENERATION_PAR_DEFAUT = "18:00"
HEURE_LIMITE_CONFIRMATION_PAR_DEFAUT = "20:00"


def creneaux(entreprise):
    """Créneaux de livraison proposés dans les commandes."""
    return list((entreprise.options or {}).get("creneaux") or CRENEAUX_PAR_DEFAUT)


def _heure(entreprise, cle, defaut):
    texte = (entreprise.options or {}).get(cle) or defaut
    return datetime.time.fromisoformat(texte)


def heure_generation_recurrentes(entreprise):
    """Heure de génération, la veille, des commandes récurrentes."""
    return _heure(entreprise, "heure_generation_recurrentes", HEURE_GENERATION_PAR_DEFAUT)


def heure_limite_confirmation(entreprise):
    """Heure limite, la veille, de confirmation des commandes récurrentes."""
    return _heure(entreprise, "heure_limite_confirmation", HEURE_LIMITE_CONFIRMATION_PAR_DEFAUT)
