"""Départ de la tournée : cascade vers les commandes et les livraisons (§7, §10)."""

import pytest
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied

from commandes.models import Commande, Statut
from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from logistique import services
from logistique.models import (
    Livraison,
    StatutChargement,
    StatutLivraison,
    StatutTournee,
)
from logistique.services import ActionImpossible
from logistique.tests.factories import PrestataireFactory, TourneeFactory
from logistique.tests.test_services_controle import (
    affecter,
    recues,
    statut,
    verifier,
)
from logistique.tests.test_services_tournee import DEMAIN, forcer
from noyau.contexte import activer_entreprise, activer_utilisateur

pytestmark = pytest.mark.django_db
R = Role


@pytest.fixture
def entreprise():
    entreprise = EntrepriseFactory()
    with activer_entreprise(entreprise):
        yield entreprise


@pytest.fixture
def tournee(entreprise, personnes):
    return TourneeFactory(entreprise=entreprise, date=DEMAIN)


def recue(tournee, personnes):
    """Commande affectée, vérifiée par le préparateur et reçue par le livreur."""
    chargement = verifier(affecter(tournee, personnes), personnes)
    with activer_utilisateur(tournee.chauffeur):
        services.confirmer_reception(chargement, recues(chargement, [60, 20]))
    return chargement


def demarrer(tournee, utilisateur=None):
    with activer_utilisateur(utilisateur or tournee.chauffeur):
        return services.demarrer_tournee(tournee)


def test_depart_cascade_commandes_et_livraisons(tournee, personnes):
    premier, second = recue(tournee, personnes), recue(tournee, personnes)
    tournee = demarrer(tournee)
    assert tournee.statut == StatutTournee.EN_COURS and tournee.depart_le
    for chargement in (premier, second):
        assert statut(chargement.commande) == Statut.EN_LIVRAISON
        livraison = Livraison.objects.get(chargement=chargement)
        assert livraison.commande == chargement.commande
        assert livraison.statut == StatutLivraison.EN_ATTENTE


def test_depart_fige_la_tournee(tournee, personnes):
    chargement = recue(tournee, personnes)
    demarrer(tournee)
    with activer_utilisateur(personnes.gestionnaire):
        with pytest.raises(ActionImpossible, match="partie"):
            services.retirer_commande(chargement)
        with pytest.raises(ActionImpossible, match="partie"):
            services.affecter_commande(tournee, chargement.commande)
    with pytest.raises(ActionImpossible):
        demarrer(tournee)
    assert Livraison.objects.count() == 1


def test_depart_bloque_si_une_commande_n_est_pas_recue(tournee, personnes):
    recue(tournee, personnes)
    verifier(affecter(tournee, personnes), personnes)
    with pytest.raises(ActionImpossible):
        demarrer(tournee)
    assert statut(tournee) == StatutTournee.EN_CHARGEMENT
    assert not Livraison.objects.exists()


def test_depart_avec_exception_acceptee(tournee, personnes):
    recue(tournee, personnes)
    bloque = affecter(tournee, personnes)
    with activer_utilisateur(personnes.gestionnaire):
        demande = services.demander_exception_depart(bloque, "Client prévenu")
    with activer_utilisateur(personnes.direction):
        services.decider_exception_chargement(demande, accepter=True)
    demarrer(tournee)
    assert statut(bloque.commande) == Statut.EN_LIVRAISON
    assert Livraison.objects.filter(chargement=bloque).exists()


def test_commande_retiree_ne_part_pas(tournee, personnes):
    recue(tournee, personnes)
    retire = affecter(tournee, personnes)
    with activer_utilisateur(personnes.gestionnaire):
        services.retirer_commande(retire)
    demarrer(tournee)
    assert statut(retire.commande) == Statut.PRETE_CHARGEMENT
    assert not Livraison.objects.filter(chargement=retire).exists()


@pytest.mark.parametrize(
    "personne", ["gestionnaire", "direction", "preparateur", "autre_chauffeur"]
)
def test_seul_le_livreur_demarre(tournee, personnes, personne):
    recue(tournee, personnes)
    with pytest.raises(PermissionDenied):
        demarrer(tournee, getattr(personnes, personne))
    assert statut(tournee) == StatutTournee.DEPART_AUTORISE


def test_prestataire_sans_compte_le_gestionnaire_demarre(entreprise, personnes):
    prestataire = PrestataireFactory(entreprise=entreprise)
    tournee = TourneeFactory(
        entreprise=entreprise, date=DEMAIN, chauffeur=None, prestataire=prestataire
    )
    chargement = affecter(tournee, personnes)
    forcer(chargement, StatutChargement.RECUE_CHAUFFEUR)
    forcer(tournee, StatutTournee.DEPART_AUTORISE)
    assert demarrer(tournee, personnes.gestionnaire).statut == StatutTournee.EN_COURS


def test_depart_refuse_si_une_commande_a_change_de_statut(tournee, personnes):
    chargement = recue(tournee, personnes)
    forcer(chargement.commande, Statut.ANNULEE)
    with pytest.raises(ActionImpossible, match="la retirer"):
        demarrer(tournee)
    assert statut(tournee) == StatutTournee.DEPART_AUTORISE


def test_depart_annule_tout_en_cas_d_erreur(tournee, personnes, monkeypatch):
    premier, second = recue(tournee, personnes), recue(tournee, personnes)
    appels = []

    def creer(**kwargs):
        if appels:
            raise RuntimeError("panne")
        appels.append(kwargs)
        return Livraison(**kwargs).save()

    monkeypatch.setattr(Livraison.objects, "create", creer)
    with pytest.raises(RuntimeError):
        demarrer(tournee)
    assert statut(tournee) == StatutTournee.DEPART_AUTORISE
    assert {statut(premier.commande), statut(second.commande)} == {Statut.PRETE_CHARGEMENT}
    assert not Livraison.objects.exists()


def test_isolation_entre_entreprises(tournee, personnes):
    recue(tournee, personnes)
    with activer_entreprise(None):
        autre = EntrepriseFactory()
        etranger = UtilisateurFactory(entreprise=autre, roles=[R.CHAUFFEUR])
    with activer_entreprise(autre), activer_utilisateur(etranger):
        with pytest.raises(ObjectDoesNotExist):
            services.demarrer_tournee(tournee)
        assert not Commande.objects.exists()
