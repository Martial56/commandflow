import datetime
import html
import re

import pytest
from django.urls import reverse

from catalogue.tests.factories import ProduitFactory
from clients.tests.factories import AdresseFactory
from commandes import services
from commandes.models import (
    Commande,
    LigneCommande,
    LigneModeleRecurrent,
    ModeleRecurrent,
    Statut,
)
from commandes.tasks import generer_recurrentes
from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.models import Entreprise
from entreprises.tests.factories import EntrepriseFactory
from noyau.contexte import activer_entreprise, activer_utilisateur

pytestmark = pytest.mark.django_db
JEUDI = datetime.date(2026, 10, 15)  # jeudi


@pytest.fixture(autouse=True)
def sans_2fa_obligatoire(settings):
    settings.MIDDLEWARE = [
        m for m in settings.MIDDLEWARE if "DoubleAuthentificationObligatoire" not in m
    ]


@pytest.fixture
def entreprise():
    return EntrepriseFactory(slug="mie-dore")


@pytest.fixture
def produits(entreprise):
    return {
        "baguette": ProduitFactory(entreprise=entreprise, nom="Baguette", prix_reference=250),
        "croissant": ProduitFactory(entreprise=entreprise, nom="Croissant", prix_reference=300),
    }


def modele(entreprise, produits, nom="Supérette Les Palmiers", jours=(0, 1, 2, 3, 4, 5), **champs):
    adresse = AdresseFactory(client__entreprise=entreprise, client__nom=nom, par_defaut=True)
    with activer_entreprise(entreprise):
        recurrent = ModeleRecurrent.objects.create(
            client=adresse.client,
            adresse=adresse,
            jours_semaine=list(jours),
            creneau="08h–10h",
            date_debut=champs.pop("date_debut", datetime.date(2026, 1, 1)),
            **champs,
        )
        LigneModeleRecurrent.objects.create(
            modele=recurrent, produit=produits["baguette"], quantite=100
        )
        LigneModeleRecurrent.objects.create(
            modele=recurrent, produit=produits["croissant"], quantite=60
        )
    return recurrent


def generer(entreprise, date=JEUDI):
    with activer_entreprise(entreprise):
        return services.generer_commandes_recurrentes(date)


# --- Génération ------------------------------------------------------------------


def test_generation_a_confirmer_avec_prix_du_jour(entreprise, produits):
    recurrent = modele(entreprise, produits)
    rapport = generer(entreprise)
    commande = rapport.creees[0]
    assert (commande.statut, commande.modele_recurrent, commande.date_livraison) == (
        Statut.A_CONFIRMER, recurrent, JEUDI,
    )  # fmt: skip
    assert (commande.canal, commande.libelle_canal, commande.cree_par) == ("", "Récurrente", None)
    lignes = LigneCommande.toutes_entreprises.filter(commande=commande).order_by("pk")
    assert [(ligne.quantite, ligne.prix_unitaire) for ligne in lignes] == [(100, 250), (60, 300)]


def test_relance_sans_doublon(entreprise, produits):
    modele(entreprise, produits)
    assert len(generer(entreprise).creees) == 1
    assert generer(entreprise).creees == []
    assert Commande.toutes_entreprises.count() == 1


@pytest.mark.parametrize(
    ("champs", "generee"),
    [
        ({"jours": (0, 1, 2)}, False),  # pas le jeudi
        ({"actif": False}, False),
        ({"date_debut": datetime.date(2026, 11, 1)}, False),
        ({"date_fin": datetime.date(2026, 10, 14)}, False),
        ({"date_fin": JEUDI}, True),
    ],
)
def test_jours_periode_et_activite(entreprise, produits, champs, generee):
    modele(entreprise, produits, **champs)
    assert bool(generer(entreprise).creees) is generee


def test_modele_en_erreur_signale_sans_bloquer_les_autres(entreprise, produits):
    a_bloquer = modele(entreprise, produits, nom="Client désactivé")
    a_bloquer.client.actif = False
    a_bloquer.client.save()
    sans_produit = modele(entreprise, produits, nom="Sans produit actif")
    for produit in produits.values():
        produit.actif = False
        produit.save()
    rapport = generer(entreprise)
    assert rapport.creees == []
    assert {m for m, _ in rapport.ignorees} == {a_bloquer, sans_produit}


def test_generation_limitee_a_l_entreprise(entreprise, produits):
    modele(entreprise, produits)
    ailleurs = EntrepriseFactory()
    modele(ailleurs, {k: ProduitFactory(entreprise=ailleurs) for k in produits})
    generer(entreprise)
    assert {c.entreprise_id for c in Commande.toutes_entreprises.all()} == {entreprise.pk}


def test_historique_de_la_commande_generee(entreprise, produits):
    from commandes.historique import historique

    modele(entreprise, produits)
    commande = generer(entreprise).creees[0]
    with activer_entreprise(entreprise):
        entrees = historique(commande)
    assert entrees[-1].texte.startswith(
        "Générée depuis le modèle récurrent « Supérette Les Palmiers"
    )
    assert entrees[-1].auteur == "Système"


# --- Tâche planifiée --------------------------------------------------------------


def test_tache_attend_l_heure_de_generation(entreprise, produits):
    modele(entreprise, produits)
    assert generer_recurrentes(maintenant="2026-10-14T17:59:00") == {}
    resultat = generer_recurrentes(maintenant="2026-10-14T18:00:00")
    assert len(resultat["mie-dore"]["creees"]) == 1
    assert Commande.toutes_entreprises.get().date_livraison == JEUDI


def test_tache_heure_reglee_par_entreprise(entreprise, produits):
    Entreprise.objects.filter(pk=entreprise.pk).update(
        options={"heure_generation_recurrentes": "16:00"}
    )
    modele(entreprise, produits)
    assert len(generer_recurrentes(maintenant="2026-10-14T16:30:00")["mie-dore"]["creees"]) == 1


def test_tache_planifiee_toutes_les_15_minutes(settings):
    planning = settings.CELERY_BEAT_SCHEDULE["generer-recurrentes"]
    assert planning["task"] == "commandes.tasks.generer_recurrentes"
    assert planning["schedule"].minute == {0, 15, 30, 45}


# --- Écran (maquette 11) -----------------------------------------------------------


@pytest.fixture
def generees(entreprise, produits):
    modele(entreprise, produits, nom="Supérette Les Palmiers")
    modele(entreprise, produits, nom="Café Le Plateau")
    return {c.client.nom: c for c in generer(entreprise).creees}


def ecran(client, utilisateur):
    client.force_login(utilisateur)
    return html.unescape(
        client.get(reverse("ecran_recurrentes"), {"date": JEUDI.isoformat()}).content.decode()
    )


def action(client, operation, *commandes, une=None):
    url = reverse("recurrentes_action")
    if une:
        url += f"?operation={operation}&une={une.pk}"
        donnees = {"date": JEUDI.isoformat()}
    else:
        donnees = {
            "operation": operation,
            "commande": [c.pk for c in commandes],
            "date": JEUDI.isoformat(),
        }
    return client.post(url, donnees, follow=True)


def statuts(generees):
    return {nom: Commande.toutes_entreprises.get(pk=c.pk).statut for nom, c in generees.items()}


def test_ecran_conforme_a_la_maquette(client, entreprise, generees):
    contenu = ecran(client, UtilisateurFactory(entreprise=entreprise, roles=[Role.GESTIONNAIRE]))
    for texte in [
        "Générées automatiquement", "Récurrentes à confirmer · jeu. 15 oct.",
        "Gérer les modèles récurrents", "Heure limite : 20h00", "0 / 2 confirmées",
        "Quantités habituelles", "Baguette 100 · Croissant 60", "08h–10h", "Confirmer", "Ajuster",
        "Marquer « pas de livraison »", "Confirmer la sélection",
    ]:  # fmt: skip
        assert texte in contenu, texte


def test_confirmer_une_commande(client, entreprise, generees):
    commercial = UtilisateurFactory(entreprise=entreprise, roles=[Role.COMMERCIAL])
    client.force_login(commercial)
    contenu = html.unescape(
        action(client, "confirmer", une=generees["Café Le Plateau"]).content.decode()
    )
    assert "1 commande(s) confirmée(s)." in contenu and "1 / 2 confirmées" in contenu
    assert statuts(generees) == {
        "Café Le Plateau": "confirmee",
        "Supérette Les Palmiers": "a_confirmer",
    }


def test_confirmer_la_selection(client, entreprise, generees):
    client.force_login(UtilisateurFactory(entreprise=entreprise, roles=[Role.GESTIONNAIRE]))
    action(client, "confirmer", *generees.values())
    assert set(statuts(generees).values()) == {"confirmee"}


def test_pas_de_livraison(client, entreprise, generees):
    client.force_login(UtilisateurFactory(entreprise=entreprise, roles=[Role.GESTIONNAIRE]))
    action(client, "pas_de_livraison", generees["Café Le Plateau"])
    commande = Commande.toutes_entreprises.get(pk=generees["Café Le Plateau"].pk)
    assert (commande.statut, commande.motif_annulation) == ("annulee", "Pas de livraison")
    contenu = ecran(client, UtilisateurFactory(entreprise=entreprise, roles=[Role.GESTIONNAIRE]))
    assert "Pas de livraison demain" in contenu and "0 / 1 confirmées" in contenu


def test_pas_de_livraison_par_le_commercial(client, entreprise, generees):
    # Décision 8.1 (19 octobre 2026) : le Commercial peut marquer « pas de livraison ».
    client.force_login(UtilisateurFactory(entreprise=entreprise, roles=[Role.COMMERCIAL]))
    action(client, "pas_de_livraison", generees["Café Le Plateau"])
    assert statuts(generees)["Café Le Plateau"] == "annulee"


def test_commercial_n_annule_pas_une_commande_saisie_par_un_autre(entreprise):
    from commandes.tests.factories import CommandeFactory

    commercial = UtilisateurFactory(entreprise=entreprise, roles=[Role.COMMERCIAL])
    with activer_entreprise(entreprise):
        saisie = CommandeFactory(adresse__client__entreprise=entreprise)
    assert not commercial.has_perm("commandes.annuler_commande", saisie)


@pytest.mark.parametrize("role", [Role.FACTURATION, Role.PRODUCTION])
def test_pas_de_livraison_refuse_aux_autres_roles(client, entreprise, generees, role):
    client.force_login(UtilisateurFactory(entreprise=entreprise, roles=[role]))
    action(client, "pas_de_livraison", generees["Café Le Plateau"])
    assert statuts(generees)["Café Le Plateau"] == "a_confirmer"


def test_ajuster_puis_confirmee_ajustee(client, entreprise, produits, generees):
    commande = generees["Supérette Les Palmiers"]
    gestionnaire = UtilisateurFactory(entreprise=entreprise, roles=[Role.GESTIONNAIRE])
    with activer_entreprise(entreprise), activer_utilisateur(gestionnaire):
        services.modifier_commande(
            commande, lignes=[(produits["baguette"], 120), (produits["croissant"], 60)]
        )
        services.confirmer_commande(commande)
    contenu = ecran(client, gestionnaire)
    texte = " ".join(re.sub(r"<[^>]+>", "", contenu).split())
    assert "Baguette 100 → 120 · Croissant 60" in texte
    assert '<strong class="font-semibold text-encre">120</strong>' in contenu
    assert "Confirmée · ajustée" in contenu


def test_lien_ajuster_revient_sur_l_ecran(client, entreprise, generees):
    commande = generees["Café Le Plateau"]
    client.force_login(UtilisateurFactory(entreprise=entreprise, roles=[Role.GESTIONNAIRE]))
    retour = f"{reverse('ecran_recurrentes')}?date={JEUDI.isoformat()}"
    url = f"{reverse('commande_modifier', args=[commande.pk])}?suivant={retour}"
    assert f'name="suivant" value="{retour}"' in html.unescape(client.get(url).content.decode())
    externe = f"{reverse('commande_modifier', args=[commande.pk])}?suivant=https://exemple.com/"
    assert 'name="suivant"' not in client.get(externe).content.decode()


@pytest.mark.parametrize("role", [Role.FACTURATION, Role.CHAUFFEUR])
def test_ecran_refuse(client, entreprise, role, generees):
    client.force_login(UtilisateurFactory(entreprise=entreprise, roles=[role]))
    assert client.get(reverse("ecran_recurrentes")).status_code == 403
    assert action(client, "confirmer", *generees.values()).status_code == 403


def test_commandes_d_une_autre_entreprise_ignorees(client, entreprise, generees):
    etranger = UtilisateurFactory(roles=[Role.GESTIONNAIRE])
    client.force_login(etranger)
    assert "Supérette" not in ecran(client, etranger)
    action(client, "confirmer", *generees.values())
    assert set(statuts(generees).values()) == {"a_confirmer"}


# --- Gestion des modèles (administration) ------------------------------------------------


def test_creation_d_un_modele_dans_l_administration(client, entreprise, produits):
    gestionnaire = UtilisateurFactory(entreprise=entreprise, roles=[Role.GESTIONNAIRE])
    adresse = AdresseFactory(client__entreprise=entreprise, par_defaut=True)
    client.force_login(gestionnaire)
    url = reverse("admin:commandes_modelerecurrent_add")
    assert client.get(url).status_code == 200
    reponse = client.post(
        url,
        {
            "client": adresse.client_id, "adresse": adresse.pk, "jours_semaine": ["0", "3"],
            "creneau": "06h–08h", "date_debut": "2026-10-01", "actif": "on",
            "lignes-TOTAL_FORMS": "1", "lignes-INITIAL_FORMS": "0",
            "lignes-0-produit": produits["baguette"].pk, "lignes-0-quantite": "50",
        },
    )  # fmt: skip
    assert reponse.status_code == 302, reponse.content.decode()[:2000]
    recurrent = ModeleRecurrent.toutes_entreprises.get()
    assert (recurrent.entreprise, recurrent.jours_semaine, recurrent.cree_par) == (
        entreprise, [0, 3], gestionnaire,
    )  # fmt: skip
    assert recurrent.lignes.model.toutes_entreprises.get(modele=recurrent).quantite == 50


def test_administration_des_modeles_refusee_au_commercial(client, entreprise):
    client.force_login(UtilisateurFactory(entreprise=entreprise, roles=[Role.COMMERCIAL]))
    reponse = client.get(reverse("admin:commandes_modelerecurrent_changelist"))
    assert reponse.status_code == 302
