"""Affichage à la française : montants en FCFA et quantités."""

import datetime
from decimal import Decimal, InvalidOperation

from django import template
from django.utils import timezone

register = template.Library()

ESPACE_FINE = " "


@register.filter
def fcfa(valeur):
    """39000 → « 39 000 » (espace fine insécable)."""
    try:
        return f"{int(Decimal(valeur)):,}".replace(",", ESPACE_FINE)
    except (TypeError, ValueError, InvalidOperation):
        return valeur


@register.filter
def quantite(valeur):
    """Decimal('120.000') → « 120 » ; Decimal('2.500') → « 2,5 »."""
    try:
        texte = format(Decimal(valeur).normalize(), "f")
    except (TypeError, ValueError, InvalidOperation):
        return valeur
    return texte.replace(".", ",")


JOURS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]
MOIS = [
    "janv.",
    "févr.",
    "mars",
    "avr.",
    "mai",
    "juin",
    "juil.",
    "août",
    "sept.",
    "oct.",
    "nov.",
    "déc.",
]


def _locale(valeur):
    if isinstance(valeur, datetime.datetime) and timezone.is_aware(valeur):
        return timezone.localtime(valeur)
    return valeur


@register.filter
def jour_mois(valeur):
    """« 15 oct. » (maquettes 9 et 10)."""
    if not valeur:
        return ""
    valeur = _locale(valeur)
    return f"{valeur.day} {MOIS[valeur.month - 1]}"


@register.filter
def date_courte(valeur):
    """« jeu. 15 oct. »."""
    if not valeur:
        return ""
    valeur = _locale(valeur)
    return f"{JOURS[valeur.weekday()]} {jour_mois(valeur)}"


@register.filter
def heure(valeur):
    """« 17h02 », à l'heure d'Abidjan."""
    if not valeur:
        return ""
    return _locale(valeur).strftime("%Hh%M")
