"""La tournée sur la fiche commande (maquette 10) : résumé, historique, demandes bloquées."""

import pytest
from django.urls import reverse

from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from logistique import services
from logistique.models import MotifEcart
from logistique.tests.factories import TourneeFactory
from logistique.tests.test_services_controle import affecter, compter, recues, verifier
from logistique.tests.test_services_tournee import DEMAIN
from noyau.contexte import activer_entreprise, activer_utilisateur

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def sans_2fa_obligatoire(settings):
    settings.MIDDLEWARE = [
        m for m in settings.MIDDLEWARE if "DoubleAuthentificationObligatoire" not in m
    ]


@pytest.fixture
def entreprise():
    entreprise = EntrepriseFactory()
    with activer_entreprise(entreprise):
        yield entreprise


@pytest.fixture
def tournee(entreprise, personnes):
    return TourneeFactory(entreprise=entreprise, date=DEMAIN)


def fiche(client, personne, commande):
    client.force_login(personne)
    return client.get(reverse("commande_detail", args=[commande.pk])).content.decode()


def test_commande_non_affectee(client, entreprise, personnes, tournee):
    chargement = affecter(tournee, personnes)
    with activer_utilisateur(personnes.gestionnaire):
        services.retirer_commande(chargement)
    texte = fiche(client, personnes.gestionnaire, chargement.commande)
    assert "Non affectée" in texte
    assert "Demander une modification" in texte


def test_resume_de_la_tournee(client, personnes, tournee):
    chargement = verifier(affecter(tournee, personnes), personnes)
    texte = fiche(client, personnes.gestionnaire, chargement.commande)
    assert (
        f"{tournee.numero}</a> · {tournee.chauffeur} · arrêt 1 · vérifiée par la préparation"
        in texte
    )
    assert f"{reverse('ecran_tournees')}?date={DEMAIN.isoformat()}" in texte


def test_lien_vers_les_tournees_selon_le_role(client, entreprise, personnes, tournee):
    chargement = affecter(tournee, personnes)
    commercial = UtilisateurFactory(entreprise=entreprise, roles=[Role.COMMERCIAL])
    texte = fiche(client, commercial, chargement.commande)
    assert tournee.numero in texte and reverse("ecran_tournees") not in texte


def test_demandes_remplacees_par_l_explication(client, personnes, tournee):
    chargement = affecter(tournee, personnes)
    texte = fiche(client, personnes.gestionnaire, chargement.commande)
    assert f"Commande dans la tournée {tournee.numero} : la retirer" in texte
    assert "Demander une modification" not in texte
    assert "Demander l&#x27;annulation" not in texte and "Demander l'annulation" not in texte


def test_historique_du_chargement(client, personnes, tournee):
    chargement = affecter(tournee, personnes)
    compter(chargement, [30, 20], personnes.preparateur, motif=MotifEcart.PRODUIT_ABIME)
    with activer_utilisateur(personnes.preparateur):
        services.signaler_ecart(chargement, "Fournée ratée")
        services.completer_chargement(chargement)
    verifier(chargement, personnes)
    with activer_utilisateur(tournee.chauffeur):
        services.confirmer_reception(chargement, recues(chargement, [60, 20]))
        services.demarrer_tournee(tournee)
    texte = fiche(client, personnes.gestionnaire, chargement.commande)
    for attendu in (
        f"Affectée à la tournée {tournee.numero} (arrêt 1)",
        "Contrôle du chargement commencé",
        "Écart constaté au chargement",
        "Demande de validation pour l&#x27;écart au chargement : « Fournée ratée »",
        "Marchandise complétée : nouveau contrôle",
        "Chargement vérifié par la préparation",
        "Réception confirmée par le livreur",
        "Départ de la tournée : en livraison",
    ):
        assert attendu in texte, attendu
    assert "partie à" in texte
    assert str(personnes.preparateur) in texte and str(tournee.chauffeur) in texte


def test_historique_retrait_et_exception(client, personnes, tournee):
    chargement = affecter(tournee, personnes)
    with activer_utilisateur(personnes.gestionnaire):
        demande = services.demander_exception_depart(chargement, "Client prévenu")
    with activer_utilisateur(personnes.direction):
        services.decider_exception_chargement(demande, accepter=False)
    with activer_utilisateur(personnes.gestionnaire):
        services.retirer_commande(chargement)
    texte = fiche(client, personnes.gestionnaire, chargement.commande)
    assert "Demande de validation pour le départ sans réception : « Client prévenu »" in texte
    assert "Exception pour le départ sans réception refusée" in texte
    assert f"Retirée de la tournée {tournee.numero}" in texte
