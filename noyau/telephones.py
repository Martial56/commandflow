"""Numéros de téléphone : indicatif +225 ajouté aux numéros nationaux (lot B9)."""

import re

INDICATIF_PAR_DEFAUT = "+225"
SEPARATEURS = re.compile(r"[ .\-]")


def completer_indicatif(valeur):
    """« 07 11 22 33 44 » → « +225 07 11 22 33 44 » : un numéro national à 10 chiffres
    (espaces, points et tirets ignorés) reçoit l'indicatif de la Côte d'Ivoire, en gardant
    la présentation saisie. Tout autre numéro (avec indicatif, autre longueur) est rendu
    tel quel, sans espaces autour."""
    valeur = (valeur or "").strip()
    chiffres = SEPARATEURS.sub("", valeur)
    if len(chiffres) == 10 and chiffres.isdigit():
        return f"{INDICATIF_PAR_DEFAUT} {valeur}"
    return valeur
