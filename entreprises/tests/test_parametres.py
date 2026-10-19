import datetime

from entreprises.models import Entreprise
from entreprises.parametres import (
    CRENEAUX_PAR_DEFAUT,
    creneaux,
    heure_generation_recurrentes,
    heure_limite_confirmation,
)


def test_valeurs_par_defaut():
    entreprise = Entreprise(options={})
    assert creneaux(entreprise) == ["06h–08h", "08h–10h", "10h–12h", "16h–18h"]
    assert heure_generation_recurrentes(entreprise) == datetime.time(18, 0)
    assert heure_limite_confirmation(entreprise) == datetime.time(20, 0)


def test_valeurs_reglees_par_l_entreprise():
    entreprise = Entreprise(
        options={
            "creneaux": ["05h–07h"],
            "heure_generation_recurrentes": "17:30",
            "heure_limite_confirmation": "21:00",
        }
    )
    assert creneaux(entreprise) == ["05h–07h"]
    assert heure_generation_recurrentes(entreprise) == datetime.time(17, 30)
    assert heure_limite_confirmation(entreprise) == datetime.time(21, 0)
    assert CRENEAUX_PAR_DEFAUT == ["06h–08h", "08h–10h", "10h–12h", "16h–18h"]
