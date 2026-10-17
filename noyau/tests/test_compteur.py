import datetime
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connection, transaction

from entreprises.tests.factories import EntrepriseFactory
from noyau.contexte import AucuneEntrepriseCourante, activer_entreprise
from noyau.services import prochain_numero


@pytest.mark.django_db
def test_numerotation_annuelle(entreprise):
    date = datetime.date(2026, 10, 17)
    assert prochain_numero("CMD", entreprise=entreprise, date_reference=date) == "CMD-2026-000001"
    assert prochain_numero("CMD", entreprise=entreprise, date_reference=date) == "CMD-2026-000002"


@pytest.mark.django_db
def test_remise_a_un_a_la_nouvelle_annee(entreprise):
    prochain_numero("CMD", entreprise=entreprise, date_reference=datetime.date(2026, 12, 31))
    numero = prochain_numero("CMD", entreprise=entreprise, date_reference=datetime.date(2027, 1, 1))
    assert numero == "CMD-2027-000001"


@pytest.mark.django_db
def test_numerotation_continue_sans_annee(entreprise):
    assert prochain_numero("CLI", annuel=False, entreprise=entreprise) == "CLI-000001"
    assert prochain_numero("CLI", annuel=False, entreprise=entreprise) == "CLI-000002"


@pytest.mark.django_db
def test_prefixes_et_entreprises_independants():
    entreprise_a = EntrepriseFactory()
    entreprise_b = EntrepriseFactory()
    date = datetime.date(2026, 1, 1)
    prochain_numero("CMD", entreprise=entreprise_a, date_reference=date)
    assert prochain_numero("CMD", entreprise=entreprise_b, date_reference=date) == "CMD-2026-000001"
    assert prochain_numero("TRN", entreprise=entreprise_a, date_reference=date) == "TRN-2026-000001"


@pytest.mark.django_db
def test_entreprise_courante_par_defaut(entreprise):
    with activer_entreprise(entreprise):
        numero = prochain_numero("FAC", date_reference=datetime.date(2026, 5, 1))
    assert numero == "FAC-2026-000001"


@pytest.mark.django_db
def test_refus_sans_entreprise():
    with pytest.raises(AucuneEntrepriseCourante):
        prochain_numero("CMD")


@pytest.mark.django_db
def test_numero_non_consomme_si_la_transaction_est_annulee(entreprise):
    date = datetime.date(2026, 3, 1)
    with pytest.raises(RuntimeError), transaction.atomic():
        prochain_numero("CMD", entreprise=entreprise, date_reference=date)
        raise RuntimeError("erreur dans le service appelant")
    assert prochain_numero("CMD", entreprise=entreprise, date_reference=date) == "CMD-2026-000001"


@pytest.mark.django_db(transaction=True)
def test_appels_simultanes_sans_doublon():
    entreprise = EntrepriseFactory()
    date = datetime.date(2026, 6, 1)

    def attribuer(_):
        try:
            return prochain_numero("CMD", entreprise=entreprise, date_reference=date)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=8) as executeur:
        numeros = list(executeur.map(attribuer, range(40)))

    assert len(set(numeros)) == 40
    assert sorted(numeros) == [f"CMD-2026-{n:06d}" for n in range(1, 41)]
