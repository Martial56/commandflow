"""Reliquats (§12.3, §22-20, décisions 8.4 et du 3 novembre 2026) : nouvelle date et plan
de production, affectation à une tournée, livraison du reliquat (complète, partielle,
échec), solde validé ; écran Tournées et pastille."""

import datetime
import html
import re
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied
from django.urls import reverse

from commandes.models import Statut
from entreprises.tests.factories import EntrepriseFactory
from logistique import alertes, services
from logistique.models import (
    Livraison,
    MotifEchec,
    MoyenAccord,
    Reliquat,
    StatutTournee,
)
from logistique.services import ActionImpossible, Resultat
from logistique.tests.factories import TourneeFactory
from logistique.tests.test_depart import demarrer, recue
from logistique.tests.test_services_controle import compter, statut
from logistique.tests.test_services_tournee import DEMAIN, forcer
from noyau.contexte import activer_entreprise, activer_utilisateur
from noyau.models import Validation
from noyau.services import ValidationPropreDemande
from production.models import PlanProduction

pytestmark = pytest.mark.django_db
APRES_DEMAIN = DEMAIN + datetime.timedelta(days=1)


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


def cloturer(livraison, resultat, livres=None, **kwargs):
    with activer_utilisateur(livraison.tournee.chauffeur):
        if livres is not None:
            kwargs["quantites"] = dict(zip(livraison.lignes.order_by("pk"), livres, strict=True))
        return services.cloturer_livraison(
            livraison, resultat, receptionnaire_nom="Mme Bamba", **kwargs
        )


def partielle(livraison, livres):
    return cloturer(
        livraison,
        Resultat.PARTIELLE,
        livres,
        motif=MotifEchec.MARCHANDISE_MANQUANTE,
        accord={"nom_client": "M. Kouassi", "moyen": MoyenAccord.ORAL},
    )


@pytest.fixture
def commande(entreprise, personnes):
    """Commande livrée à 40 sur 60 et 20 sur 20 : reliquat de 20 sur la première ligne."""
    tournee = TourneeFactory(entreprise=entreprise, date=DEMAIN)
    chargement = recue(tournee, personnes)
    demarrer(tournee)
    partielle(Livraison.objects.get(chargement=chargement), [40, 20])
    commande = chargement.commande
    commande.refresh_from_db()
    assert commande.statut == Statut.PARTIELLEMENT_LIVREE
    return commande


def reliquat(commande):
    return Reliquat.objects.get(commande_origine=commande, traite=False)


def planifier(commande, personnes, date=APRES_DEMAIN):
    with activer_utilisateur(personnes.gestionnaire):
        return services.planifier_reliquat(commande, date)


def affecter_reliquat(entreprise, commande, personnes, date=APRES_DEMAIN):
    tournee = TourneeFactory(entreprise=entreprise, date=date)
    with activer_utilisateur(personnes.gestionnaire):
        chargement = services.affecter_commande(tournee, commande)
    return tournee, chargement


def partir(tournee, chargement, personnes, quantite=20):
    """Reliquat compté, vérifié, reçu ; la tournée part."""
    compter(chargement, [quantite], personnes.preparateur)
    with activer_utilisateur(personnes.preparateur):
        services.valider_preparation(chargement)
    with activer_utilisateur(tournee.chauffeur):
        ligne = chargement.lignes.get()
        services.confirmer_reception(chargement, {ligne: quantite})
    demarrer(tournee)
    return Livraison.objects.get(chargement=chargement)


# --- Planification et production -----------------------------------------------------


def commande_au_plan(date):
    plan = PlanProduction.objects.get(date_livraison=date)
    return sum(besoin.quantite_commandee for besoin in plan.besoins.all())


def test_planifier_ajoute_le_reliquat_au_plan(commande, personnes):
    planifier(commande, personnes)
    assert reliquat(commande).nouvelle_date == APRES_DEMAIN
    assert commande_au_plan(APRES_DEMAIN) == 20


def test_changer_la_date_deplace_le_besoin(commande, personnes):
    planifier(commande, personnes)
    plus_tard = APRES_DEMAIN + datetime.timedelta(days=1)
    planifier(commande, personnes, plus_tard)
    assert commande_au_plan(APRES_DEMAIN) == 0
    assert commande_au_plan(plus_tard) == 20


def test_planifier_reserve_au_gestionnaire(commande, personnes):
    """Décision 8.4 : le Gestionnaire fixe la nouvelle date."""
    with activer_utilisateur(personnes.direction):
        with pytest.raises(PermissionDenied):
            services.planifier_reliquat(commande, APRES_DEMAIN)
    assert reliquat(commande).nouvelle_date is None


def test_planifier_refuse_une_date_passee(commande, personnes):
    with pytest.raises(ActionImpossible, match="à partir d'aujourd'hui"):
        planifier(commande, personnes, datetime.date.today() - datetime.timedelta(days=1))


# --- Affectation ---------------------------------------------------------------------


def test_reliquat_affectable_le_jour_prevu(entreprise, commande, personnes):
    assert commande not in services.commandes_affectables(APRES_DEMAIN)
    planifier(commande, personnes)
    assert list(services.commandes_affectables(APRES_DEMAIN)) == [commande]
    assert commande not in services.commandes_affectables(DEMAIN)
    tournee, chargement = affecter_reliquat(entreprise, commande, personnes)
    ligne = chargement.lignes.get()
    assert ligne.quantite_attendue == 20
    assert ligne.ligne_commande == reliquat(commande).ligne_commande
    assert reliquat(commande).chargement == chargement
    assert commande not in services.commandes_affectables(APRES_DEMAIN)


def test_reliquat_non_planifie_refuse(entreprise, commande, personnes):
    tournee = TourneeFactory(entreprise=entreprise, date=APRES_DEMAIN)
    with activer_utilisateur(personnes.gestionnaire):
        with pytest.raises(ActionImpossible, match="aucun reliquat"):
            services.affecter_commande(tournee, commande)


def test_retirer_libere_le_reliquat(entreprise, commande, personnes):
    planifier(commande, personnes)
    _, chargement = affecter_reliquat(entreprise, commande, personnes)
    with activer_utilisateur(personnes.gestionnaire):
        with pytest.raises(ActionImpossible, match="le retirer"):
            services.planifier_reliquat(commande, APRES_DEMAIN)
        services.retirer_commande(chargement)
    assert reliquat(commande).chargement is None
    assert commande in services.commandes_affectables(APRES_DEMAIN)


# --- Livraison du reliquat -----------------------------------------------------------


@pytest.fixture
def en_route(entreprise, commande, personnes):
    planifier(commande, personnes)
    tournee, chargement = affecter_reliquat(entreprise, commande, personnes)
    return partir(tournee, chargement, personnes)


def test_depart_laisse_la_commande_partiellement_livree(en_route, commande):
    assert statut(commande) == Statut.PARTIELLEMENT_LIVREE
    assert [ligne.quantite_prevue for ligne in en_route.lignes.all()] == [Decimal(20)]


def test_reliquat_livre_la_commande_est_livree(en_route, commande):
    cloturer(en_route, Resultat.LIVREE, [20])
    assert statut(commande) == Statut.LIVREE
    assert not Reliquat.objects.filter(commande_origine=commande, traite=False).exists()
    assert statut(en_route.tournee) == StatutTournee.TERMINEE


def test_reliquat_livre_partiellement(en_route, commande):
    """3 novembre : un nouveau reliquat remplace l'ancien."""
    partielle(en_route, [15])
    nouveau = reliquat(commande)
    assert (nouveau.quantite, nouveau.livraison) == (5, en_route)
    assert Reliquat.objects.filter(commande_origine=commande, traite=True).count() == 1
    assert statut(commande) == Statut.PARTIELLEMENT_LIVREE


def test_reliquat_en_echec_reste_ouvert(en_route, commande, personnes):
    """3 novembre : le gestionnaire fixe une nouvelle date ou le solde."""
    cloturer(en_route, Resultat.ECHEC, motif=MotifEchec.CLIENT_ABSENT)
    ouvert = reliquat(commande)
    assert (ouvert.chargement, ouvert.nouvelle_date) == (None, None)
    assert statut(commande) == Statut.PARTIELLEMENT_LIVREE
    assert alertes.reliquats_a_planifier().count() == 1
    planifier(commande, personnes, APRES_DEMAIN + datetime.timedelta(days=1))


# --- Solde (décision 8.4) ------------------------------------------------------------


def test_solde_valide_par_un_autre_responsable(commande, personnes):
    planifier(commande, personnes)
    with activer_utilisateur(personnes.gestionnaire):
        demande = services.demander_solde_reliquat(commande, "Le client n'en veut plus")
        with pytest.raises(ValidationPropreDemande):
            services.decider_solde_reliquat(demande, accepter=True)
        with pytest.raises(ActionImpossible, match="attend déjà"):
            services.demander_solde_reliquat(commande, "Encore")
    with activer_utilisateur(personnes.direction):
        services.decider_solde_reliquat(demande, accepter=True)
    reste = Reliquat.objects.get(commande_origine=commande)
    assert (reste.traite, reste.solde) == (True, True)
    assert statut(commande) == Statut.LIVREE
    assert commande_au_plan(APRES_DEMAIN) == 0  # plus compté dans le commandé


def test_solde_refuse(commande, personnes):
    with activer_utilisateur(personnes.gestionnaire):
        demande = services.demander_solde_reliquat(commande, "x")
    with activer_utilisateur(personnes.direction):
        services.decider_solde_reliquat(demande, accepter=False)
    assert not reliquat(commande).solde
    assert statut(commande) == Statut.PARTIELLEMENT_LIVREE


def test_solde_impossible_dans_une_tournee(entreprise, commande, personnes):
    planifier(commande, personnes)
    affecter_reliquat(entreprise, commande, personnes)
    with activer_utilisateur(personnes.gestionnaire):
        with pytest.raises(ActionImpossible, match="le retirer"):
            services.demander_solde_reliquat(commande, "x")


def test_solde_exige_un_motif(commande, personnes):
    with activer_utilisateur(personnes.gestionnaire):
        with pytest.raises(ActionImpossible, match="pourquoi"):
            services.demander_solde_reliquat(commande, " ")


def test_solde_refuse_au_chauffeur(commande, personnes):
    with activer_utilisateur(personnes.autre_chauffeur):
        with pytest.raises(PermissionDenied):
            services.demander_solde_reliquat(commande, "x")


# --- Commande relancée : à nouveau affectable ----------------------------------------


def test_commande_relancee_affectable_a_nouveau(entreprise, personnes):
    tournee = TourneeFactory(entreprise=entreprise, date=DEMAIN)
    chargement = recue(tournee, personnes)
    demarrer(tournee)
    livraison = Livraison.objects.get(chargement=chargement)
    cloturer(livraison, Resultat.ECHEC, motif=MotifEchec.CLIENT_ABSENT)
    livraison.refresh_from_db()
    with activer_utilisateur(personnes.gestionnaire):
        services.reporter_livraison(livraison, APRES_DEMAIN)
    commande = chargement.commande
    commande.refresh_from_db()
    assert commande.tournee_en_cours() is None  # plus bloquée par l'ancienne tournée
    forcer(commande, Statut.PRETE_CHARGEMENT)
    assert commande in services.commandes_affectables(APRES_DEMAIN)


# --- Écran Tournées et pastille ------------------------------------------------------


def texte(reponse):
    contenu = html.unescape(reponse.content.decode())
    return " ".join(re.sub(r"<[^>]+>", " ", contenu).split())


def tableau(client, date=APRES_DEMAIN):
    return client.get(reverse("ecran_tournees"), {"date": date.isoformat()})


def test_ecran_planifier_puis_affecter(client, commande, personnes):
    assert alertes.nombre_alertes(personnes.gestionnaire) == 1  # reliquat sans date
    client.force_login(personnes.gestionnaire)
    page = texte(tableau(client))
    produit = reliquat(commande).ligne_commande.produit.nom
    assert f"Reliquats à planifier 1 {commande.client.nom} {commande.numero} · 20 {produit}" in page
    client.post(
        reverse("reliquat_planifier", args=[commande.pk]), {"date": APRES_DEMAIN.isoformat()}
    )
    assert reliquat(commande).nouvelle_date == APRES_DEMAIN
    page = texte(tableau(client))
    assert f"{commande.client.nom} Reliquat" in page and "20 pièces" in page
    assert "Prévu le" in page
    assert alertes.nombre_alertes(personnes.gestionnaire) == 0


def test_ecran_solder(client, commande, personnes):
    client.force_login(personnes.gestionnaire)
    client.post(reverse("reliquat_solder", args=[commande.pk]), {"motif": "Client fermé"})
    page = texte(tableau(client))
    assert f"Solde demandé par {personnes.gestionnaire} : Client fermé" in page
    assert "En attente d'un autre responsable." in page
    demande = Validation.objects.get(decision=Validation.Decision.EN_ATTENTE)
    client.force_login(personnes.direction)
    client.post(reverse("solde_decider", args=[demande.pk]), {"decision": "accepter"})
    assert statut(commande) == Statut.LIVREE
    assert "Reliquats à planifier" not in texte(tableau(client))


def test_ecran_reserve(client, commande, personnes):
    client.force_login(personnes.autre_chauffeur)
    reponse = client.post(
        reverse("reliquat_planifier", args=[commande.pk]), {"date": APRES_DEMAIN.isoformat()}
    )
    assert reponse.status_code == 403
    assert reliquat(commande).nouvelle_date is None
