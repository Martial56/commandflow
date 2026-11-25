"""Indicatif +225 ajouté aux numéros nationaux (lot B9)."""

import pytest

from noyau.telephones import completer_indicatif


@pytest.mark.parametrize(
    ("saisie", "attendu"),
    [
        ("07 11 22 33 44", "+225 07 11 22 33 44"),
        ("0711223344", "+225 0711223344"),
        (" 07.11.22.33.44 ", "+225 07.11.22.33.44"),
        ("07-11-22-33-44", "+225 07-11-22-33-44"),
        ("+225 07 11 22 33 44", "+225 07 11 22 33 44"),  # déjà international
        ("+33 6 12 34 56 78", "+33 6 12 34 56 78"),  # autre pays
        ("2250711223344", "2250711223344"),  # 13 chiffres : laissé tel quel
        ("07 11 22 33", "07 11 22 33"),  # trop court
        ("", ""),
        (None, ""),
    ],
)
def test_completer_indicatif(saisie, attendu):
    assert completer_indicatif(saisie) == attendu
