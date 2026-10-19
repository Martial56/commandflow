"""Affichage à la française : montants en FCFA et quantités."""

from decimal import Decimal, InvalidOperation

from django import template

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
