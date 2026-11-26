"""Urgence après le départ (lot B3) : elle part dans une nouvelle tournée, éventuellement
du même chauffeur, qui passe d'une tournée à l'autre par le bandeau « Autres tournées
aujourd'hui » ; le livreur est notifié d'une tournée du jour qui lui est confiée."""

import datetime
import html
import re

import pytest
from django.urls import reverse
from django.utils import timezone

from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from logistique import services
from logistique.models import Livraison, StatutTournee, Tournee
from logistique.tests.factories import TourneeFactory
from logistique.tests.test_depart import demarrer
from logistique.tests.test_services_controle import recues, verifier
from logistique.tests.test_services_tournee import commande_prete
from notifications.models import Notification
from noyau.contexte import activer_entreprise, activer_utilisateur

pytestmark = pytest.mark.django_db
ECRAN = reverse("ecran_ma_tournee")
BANDEAU = "Autres tournées aujourd'hui"


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
def premiere(entreprise, personnes):
    """Tournée du jour partie, avec deux arrêts."""
    tournee = TourneeFactory(
        entreprise=entreprise, numero="TRN-2026-000000", date=timezone.localdate()
    )
    recue(tournee, personnes)
    recue(tournee, personnes)
    return demarrer(tournee)


def recue(tournee, personnes):
    """Commande du jour de la tournée, affectée, vérifiée puis reçue par le livreur."""
    with activer_utilisateur(personnes.gestionnaire):
        chargement = services.affecter_commande(
            tournee, commande_prete(tournee.entreprise, date=tournee.date)
        )
    chargement = verifier(chargement, personnes)
    with activer_utilisateur(tournee.chauffeur):
        services.confirmer_reception(chargement, recues(chargement, [60, 20]))
    return chargement


def urgence(premiere, personnes, chauffeur=None):
    """Nouvelle tournée du jour pour l'urgence, créée par le gestionnaire."""
    with activer_utilisateur(personnes.gestionnaire):
        return services.creer_tournee(
            date=timezone.localdate(), chauffeur=chauffeur or premiere.chauffeur
        )


def texte(reponse):
    contenu = html.unescape(reponse.content.decode())
    return " ".join(re.sub(r"<[^>]+>", " ", contenu).split())


def test_bandeau_vers_la_tournee_de_l_urgence(client, premiere, personnes):
    seconde = urgence(premiere, personnes)
    client.force_login(premiere.chauffeur)
    page = texte(client.get(ECRAN))
    assert premiere.numero in page and BANDEAU in page  # la tournée partie reste affichée
    assert f"{seconde.numero} Chargement à recevoir" in page
    assert reverse("reception_tournee", args=[seconde.pk]) in client.get(ECRAN).text

    # Depuis la tournée de l'urgence, retour vers la première, avec ses arrêts restants.
    page = texte(client.get(reverse("reception_tournee", args=[seconde.pk])))
    assert "Contrôle du chargement" in page
    assert f"{premiere.numero} En cours · 2 arrêts restants" in page


def test_les_deux_tournees_en_cours_en_meme_temps(client, premiere, personnes):
    """Q1 a : le chauffeur revenu au dépôt démarre la tournée de l'urgence sans avoir fini
    la première ; il passe de l'une à l'autre."""
    seconde = urgence(premiere, personnes)
    recue(seconde, personnes)
    demarrer(seconde)
    assert set(Tournee.objects.values_list("statut", flat=True)) == {StatutTournee.EN_COURS}
    client.force_login(premiere.chauffeur)
    page = texte(client.get(reverse("reception_tournee", args=[seconde.pk])))
    assert f"{premiere.numero} En cours · 2 arrêts restants" in page
    page = texte(client.get(reverse("reception_tournee", args=[premiere.pk])))
    assert f"{seconde.numero} En cours · 1 arrêt restant" in page


def test_pas_de_bandeau_pour_une_seule_tournee(client, premiere):
    client.force_login(premiere.chauffeur)
    assert BANDEAU not in texte(client.get(ECRAN))


def test_bandeau_sans_tournee_d_un_autre_jour_ni_d_un_autre_livreur(client, premiere, personnes):
    urgence(premiere, personnes, chauffeur=personnes.autre_chauffeur)
    TourneeFactory(
        entreprise=premiere.entreprise,
        numero="TRN-2026-900002",
        date=timezone.localdate() + datetime.timedelta(days=1),
        chauffeur=premiere.chauffeur,
    )
    terminee = urgence(premiere, personnes)
    Tournee.toutes_entreprises.filter(pk=terminee.pk).update(statut=StatutTournee.TERMINEE)
    client.force_login(premiere.chauffeur)
    assert BANDEAU not in texte(client.get(ECRAN))


def test_bandeau_jamais_la_tournee_d_une_autre_entreprise(client, premiere):
    with activer_entreprise(None):
        etrangere = TourneeFactory(date=timezone.localdate())
    client.force_login(premiere.chauffeur)
    assert etrangere.numero not in client.get(ECRAN).text


def test_tournee_de_l_urgence_non_terminee_apres_une_livraison(client, premiere, personnes):
    """Les arrêts restants suivent les livraisons traitées."""
    seconde = urgence(premiere, personnes)
    livraison = Livraison.objects.filter(chargement__tournee=premiere).order_by("pk").first()
    with activer_utilisateur(premiere.chauffeur):
        services.cloturer_livraison(
            livraison,
            services.Resultat.LIVREE,
            quantites=dict(zip(livraison.lignes.order_by("pk"), [60, 20], strict=True)),
            receptionnaire_nom="Mme Bamba",
        )
    client.force_login(premiere.chauffeur)
    page = texte(client.get(reverse("reception_tournee", args=[seconde.pk])))
    assert f"{premiere.numero} En cours · 1 arrêt restant" in page


# --- Notification du livreur (Q2) --------------------------------------------------------


def test_livreur_notifie_d_une_tournee_du_jour(premiere, personnes):
    seconde = urgence(premiere, personnes)
    notification = Notification.objects.get(destinataire=premiere.chauffeur)
    assert seconde.numero in notification.texte and "à recevoir" in notification.texte
    assert notification.lien == reverse("reception_tournee", args=[seconde.pk])


def test_pas_de_notification_pour_un_autre_jour(personnes, entreprise):
    chauffeur = UtilisateurFactory(entreprise=entreprise, roles=[Role.CHAUFFEUR])
    with activer_utilisateur(personnes.gestionnaire):
        services.creer_tournee(
            date=timezone.localdate() + datetime.timedelta(days=1), chauffeur=chauffeur
        )
    assert not Notification.objects.filter(destinataire=chauffeur).exists()


def test_nouveau_livreur_notifie_au_changement(premiere, personnes):
    seconde = urgence(premiere, personnes)
    with activer_utilisateur(personnes.gestionnaire):
        services.changer_livreur(seconde, chauffeur=personnes.autre_chauffeur)
        # Même livreur, autre véhicule : pas de nouvelle notification.
        services.changer_livreur(seconde, chauffeur=personnes.autre_chauffeur)
    assert Notification.objects.filter(destinataire=personnes.autre_chauffeur).count() == 1


def test_cloche_et_notifications_au_format_telephone(client, premiere, personnes):
    urgence(premiere, personnes)
    client.force_login(premiere.chauffeur)
    page = client.get(ECRAN).text
    assert "data-non-lues>1<" in page
    reponse = client.get(reverse("notifications"))
    page = texte(reponse)
    assert "Retour à mon écran" in page and "Nouvelle tournée" in page
    assert reverse("ecran_ma_tournee") in reponse.text
    assert "base_terrain.html" in [g.name for g in reponse.templates]


def test_notifications_au_bureau_inchangees(client, entreprise):
    client.force_login(UtilisateurFactory(entreprise=entreprise, roles=[Role.GESTIONNAIRE]))
    reponse = client.get(reverse("notifications"))
    assert "base_bureau.html" in [g.name for g in reponse.templates]
    assert "Retour à mon écran" not in texte(reponse)
