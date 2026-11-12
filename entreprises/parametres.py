"""Paramètres réglables par entreprise, lus dans `Entreprise.options` (décisions 8.1).

Valeurs par défaut quand l'entreprise ne les a pas encore réglées (écran Paramètres
à venir).
"""

import datetime
import re

CRENEAUX_PAR_DEFAUT = ["06h–08h", "08h–10h", "10h–12h", "16h–18h"]
HEURE_GENERATION_PAR_DEFAUT = "18:00"
HEURE_LIMITE_CONFIRMATION_PAR_DEFAUT = "20:00"
HEURE_RESUME_PAR_DEFAUT = "07:00"


def creneaux(entreprise):
    """Créneaux de livraison proposés dans les commandes."""
    return list((entreprise.options or {}).get("creneaux") or CRENEAUX_PAR_DEFAUT)


CRENEAU = re.compile(r"^\s*(\d{1,2})h(\d{2})?\s*[–-]\s*(\d{1,2})h(\d{2})?\s*$")


def bornes_creneau(creneau):
    """« 06h–08h » → (06:00, 08:00) ; None si le texte n'est pas un créneau horaire."""
    trouve = CRENEAU.match(creneau or "")
    if not trouve:
        return None
    debut_h, debut_m, fin_h, fin_m = trouve.groups()
    try:
        return (
            datetime.time(int(debut_h), int(debut_m or 0)),
            datetime.time(int(fin_h), int(fin_m or 0)),
        )
    except ValueError:  # « 25h–26h »
        return None


def _heure(entreprise, cle, defaut):
    texte = (entreprise.options or {}).get(cle) or defaut
    return datetime.time.fromisoformat(texte)


def heure_generation_recurrentes(entreprise):
    """Heure de génération, la veille, des commandes récurrentes."""
    return _heure(entreprise, "heure_generation_recurrentes", HEURE_GENERATION_PAR_DEFAUT)


def heure_limite_confirmation(entreprise):
    """Heure limite, la veille, de confirmation des commandes récurrentes."""
    return _heure(entreprise, "heure_limite_confirmation", HEURE_LIMITE_CONFIRMATION_PAR_DEFAUT)


def heure_resume(entreprise):
    """Heure du résumé quotidien et de l'alerte des paiements en retard (§16.1)."""
    return _heure(entreprise, "heure_resume", HEURE_RESUME_PAR_DEFAUT)


# Preuves exigées à la livraison (décision 8.4) : chaque exigence est une liste de moyens
# dont un seul suffit. Par défaut : photo du bordereau ou nom du réceptionnaire.
PREUVES_PAR_DEFAUT = [["photo_bordereau", "receptionnaire"]]


def preuves_obligatoires(entreprise, categorie):
    """Exigences de preuve pour une catégorie de client, lues dans
    `options["preuves"][categorie]`. Un moyen seul (« signature ») vaut une exigence
    à un seul moyen : `["photo_bordereau", "receptionnaire"]` exige donc les deux."""
    reglage = ((entreprise.options or {}).get("preuves") or {}).get(categorie)
    if reglage is None:
        return [list(exigence) for exigence in PREUVES_PAR_DEFAUT]
    return [[exigence] if isinstance(exigence, str) else list(exigence) for exigence in reglage]
