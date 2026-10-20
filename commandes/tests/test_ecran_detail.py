import datetime
import html

import pytest
from django.urls import reverse

from catalogue.tests.factories import ProduitFactory
from clients.tests.factories import AdresseFactory
from commandes import services
from commandes.models import Canal, Commande, LigneCommande, Statut
from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from noyau.contexte import activer_entreprise, activer_utilisateur
from noyau.models import Validation

pytestmark = pytest.mark.django_db
DEMAIN = datetime.date.today() + datetime.timedelta(days=1)


@pytest.fixture(autouse=True)
def sans_2fa_obligatoire(settings):
    settings.MIDDLEWARE = [
        m for m in settings.MIDDLEWARE if "DoubleAuthentificationObligatoire" not in m
    ]


@pytest.fixture
def entreprise():
    return EntrepriseFactory()


@pytest.fixture
def personnes(entreprise):
    noms = {Role.COMMERCIAL: ("Aminata", "K."), Role.DIRECTION: ("Ibrahim", "T.")}
    return {
        role: UtilisateurFactory(
            entreprise=entreprise, roles=[role], first_name=noms.get(role, ("", ""))[0],
            last_name=noms.get(role, ("", ""))[1],
        )
        for role in Role
    }  # fmt: skip


@pytest.fixture
def produits(entreprise):
    return {
        "baguette": ProduitFactory(entreprise=entreprise, nom="Pain baguette", prix_reference=250),
        "croissant": ProduitFactory(entreprise=entreprise, nom="Croissant", prix_reference=300),
    }


@pytest.fixture
def commande(entreprise, personnes, produits):
    adresse = AdresseFactory(
        client__entreprise=entreprise, client__nom="Restaurant Le Baobab", par_defaut=True
    )
    with activer_entreprise(entreprise), activer_utilisateur(personnes[Role.COMMERCIAL]):
        return services.creer_commande(
            client=adresse.client,
            lignes=[(produits["baguette"], 120), (produits["croissant"], 30)],
            canal=Canal.WHATSAPP,
            date_livraison=DEMAIN,
            creneau="06h–08h",
        )


def page(client, utilisateur, commande):
    client.force_login(utilisateur)
    return html.unescape(
        client.get(reverse("commande_detail", args=[commande.pk])).content.decode()
    )


def poster(client, nom, commande, **donnees):
    return client.post(reverse(nom, args=[commande.pk]), donnees, follow=True)


def recharger(commande):
    return Commande.toutes_entreprises.get(pk=commande.pk)


def test_fiche_conforme_a_la_maquette(client, personnes, commande):
    contenu = page(client, personnes[Role.COMMERCIAL], commande)
    for texte in [
        commande.numero, "Brouillon", "Restaurant Le Baobab", "06h–08h", "WhatsApp",
        "Confirmée", "Planifiée", "En préparation", "Prête à charger", "En livraison", "Livrée",
        "Produits", "Pain baguette", "39 000 FCFA", "Suivi lié", "Tournée", "Non affectée",
        "Facture", "Pas encore facturée", "Paiement", "Historique", "Commande créée",
    ]:  # fmt: skip
        assert texte in contenu, texte


def test_parcours_brouillon_soumission_confirmation(client, personnes, commande):
    commercial = personnes[Role.COMMERCIAL]
    contenu = page(client, commercial, commande)
    assert "Soumettre pour confirmation" in contenu and "Modifier" in contenu
    assert "Commande soumise pour confirmation." in html.unescape(
        poster(client, "commande_soumettre", commande).content.decode()
    )
    contenu = html.unescape(
        poster(client, "commande_confirmer", commande, note="OK par WhatsApp").content.decode()
    )
    assert recharger(commande).statut == Statut.A_PLANIFIER
    assert "Commande confirmée avec le client (« OK par WhatsApp »)" in contenu
    assert "Demander une modification" in contenu and "Demander l'annulation" in contenu


def test_actions_masquees_et_refusees_a_la_facturation(client, personnes, commande):
    facturation = personnes[Role.FACTURATION]
    contenu = page(client, facturation, commande)
    assert "Soumettre pour confirmation" not in contenu and "Modifier" not in contenu
    assert poster(client, "commande_soumettre", commande).status_code == 403
    assert client.get(reverse("commande_modifier", args=[commande.pk])).status_code == 403


def test_annulation_avant_confirmation(client, personnes, commande):
    client.force_login(personnes[Role.COMMERCIAL])
    contenu = html.unescape(poster(client, "commande_annuler", commande, motif="").content.decode())
    assert "Indiquer le motif de l'annulation." in contenu
    poster(client, "commande_annuler", commande, motif="Client a renoncé")
    assert recharger(commande).statut == Statut.ANNULEE
    contenu = page(client, personnes[Role.COMMERCIAL], commande)
    assert "Commande annulée — motif : « Client a renoncé »" in contenu


def test_modification_avant_confirmation(client, personnes, produits, commande):
    client.force_login(personnes[Role.COMMERCIAL])
    url = reverse("commande_modifier", args=[commande.pk])
    contenu = client.get(url).content.decode()
    assert "Modifier la commande" in contenu and 'value="120"' in contenu
    produits["baguette"].prix_reference = 275
    produits["baguette"].save()
    lignes = list(LigneCommande.toutes_entreprises.filter(commande=commande).order_by("pk"))
    client.post(
        url,
        {
            "canal": "telephone", "adresse": commande.adresse_id,
            "date_livraison": DEMAIN.isoformat(), "creneau": "08h–10h",
            f"produit_{lignes[0].pk}": produits["baguette"].pk,
            f"quantite_{lignes[0].pk}": "100",
        },
    )  # fmt: skip
    commande = recharger(commande)
    assert (commande.canal, commande.creneau) == ("telephone", "08h–10h")
    baguette = LigneCommande.toutes_entreprises.get(pk=lignes[0].pk)
    assert (baguette.quantite, baguette.prix_unitaire) == (100, 250)  # prix figé
    assert LigneCommande.toutes_entreprises.get(pk=lignes[1].pk).retiree
    contenu = page(client, personnes[Role.COMMERCIAL], commande)
    assert "Pain baguette : 120 → 100" in contenu and "Retrait : Croissant" in contenu
    assert "Créneau : 06h–08h → 08h–10h" in contenu


@pytest.fixture
def confirmee(entreprise, personnes, commande):
    with activer_entreprise(entreprise), activer_utilisateur(personnes[Role.COMMERCIAL]):
        services.soumettre_commande(commande)
        return services.confirmer_commande(commande)


def test_demande_de_modification_validee_comme_dans_la_maquette(
    client, personnes, produits, confirmee
):
    client.force_login(personnes[Role.COMMERCIAL])
    lignes = list(LigneCommande.toutes_entreprises.filter(commande=confirmee).order_by("pk"))
    url = reverse("commande_demander_modification", args=[confirmee.pk])
    assert "Demander une modification" in client.get(url).content.decode()
    client.post(
        url,
        {
            "adresse": confirmee.adresse_id, "date_livraison": DEMAIN.isoformat(),
            "creneau": "06h–08h",
            f"produit_{lignes[0].pk}": produits["baguette"].pk, f"quantite_{lignes[0].pk}": "120",
            f"produit_{lignes[1].pk}": produits["croissant"].pk, f"quantite_{lignes[1].pk}": "40",
            "motif": "demande client par WhatsApp",
        },
    )  # fmt: skip
    demande = Validation.toutes_entreprises.get(action="modification")
    assert demande.contenu["champs"] == {}  # seules les lignes changent

    contenu = page(client, personnes[Role.COMMERCIAL], confirmee)
    assert (
        "Demandes en attente de validation" in contenu and "En attente d'un responsable" in contenu
    )
    assert "Accepter" not in contenu

    contenu = page(client, personnes[Role.DIRECTION], confirmee)
    assert "Accepter" in contenu
    reponse = client.post(
        reverse("commande_decider", args=[confirmee.pk, demande.pk]),
        {"decision": "accepter"},
        follow=True,
    )
    contenu = html.unescape(reponse.content.decode())
    assert "Demande acceptée et appliquée." in contenu
    assert "Croissant : 30 → 40, validé par Ibrahim T. (« demande client par WhatsApp »)" in contenu
    assert "Commande confirmée avec le client" in contenu


def test_demande_d_annulation_refusee_puis_acceptee(client, personnes, confirmee):
    client.force_login(personnes[Role.COMMERCIAL])
    poster(client, "commande_demander_annulation", confirmee, motif="Fermeture")
    demande = Validation.toutes_entreprises.get(action="annulation")
    client.force_login(personnes[Role.COMMERCIAL])
    refus = client.post(
        reverse("commande_decider", args=[confirmee.pk, demande.pk]), {"decision": "accepter"}
    )
    assert refus.status_code == 403

    client.force_login(personnes[Role.GESTIONNAIRE])
    client.post(
        reverse("commande_decider", args=[confirmee.pk, demande.pk]), {"decision": "refuser"}
    )
    assert recharger(confirmee).statut == Statut.A_PLANIFIER
    assert "Demande de annulation refusée" not in page(
        client, personnes[Role.GESTIONNAIRE], confirmee
    )

    client.force_login(personnes[Role.COMMERCIAL])
    poster(client, "commande_demander_annulation", confirmee, motif="Fermeture exceptionnelle")
    demande = Validation.toutes_entreprises.get(action="annulation", decision="en_attente")
    client.force_login(personnes[Role.DIRECTION])
    client.post(
        reverse("commande_decider", args=[confirmee.pk, demande.pk]), {"decision": "accepter"}
    )
    assert recharger(confirmee).statut == Statut.ANNULEE
    contenu = page(client, personnes[Role.DIRECTION], confirmee)
    assert "Demande d'annulation refusée" in contenu
    assert "Commande annulée (« Fermeture exceptionnelle »), validé par Ibrahim T." in contenu


def test_frise_datee_apres_confirmation(client, personnes, confirmee):
    # Confirmée puis aussitôt intégrée au plan (Sprint 3) : « Planifiée » est atteinte.
    contenu = page(client, personnes[Role.COMMERCIAL], confirmee)
    assert '<strong class="font-semibold">Confirmée</strong>' in contenu
    assert '<strong class="font-semibold">Planifiée</strong>' in contenu
    assert '<strong class="font-semibold">En préparation</strong>' not in contenu


def test_fiche_d_une_autre_entreprise_introuvable(client, commande):
    client.force_login(UtilisateurFactory(roles=[Role.GESTIONNAIRE]))
    assert client.get(reverse("commande_detail", args=[commande.pk])).status_code == 404
    assert poster(client, "commande_soumettre", commande).status_code == 404


def test_responsable_ne_valide_pas_sa_propre_demande(client, entreprise, personnes, confirmee):
    gestionnaire, direction = personnes[Role.GESTIONNAIRE], personnes[Role.DIRECTION]
    with activer_entreprise(entreprise), activer_utilisateur(gestionnaire):
        demande = services.demander_annulation(confirmee, "Fermeture exceptionnelle")

    contenu = page(client, gestionnaire, confirmee)
    assert "En attente d'un autre responsable" in contenu
    assert 'value="accepter"' not in contenu
    url = reverse("commande_decider", args=[confirmee.pk, demande.pk])
    reponse = client.post(url, {"decision": "accepter"}, follow=True)
    assert "Un responsable ne peut pas valider sa propre demande." in html.unescape(
        reponse.content.decode()
    )
    assert recharger(confirmee).statut == Statut.A_PLANIFIER

    assert 'value="accepter"' in page(client, direction, confirmee)
    client.post(url, {"decision": "accepter"})
    assert recharger(confirmee).statut == Statut.ANNULEE
