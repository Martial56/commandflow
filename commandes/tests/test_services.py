import datetime
from contextlib import contextmanager
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from catalogue.models import Unite
from catalogue.tests.factories import ProduitFactory
from clients.tests.factories import AdresseFactory
from commandes import services
from commandes.models import Canal, Commande, LigneCommande, Statut
from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.models import Entreprise
from entreprises.tests.factories import EntrepriseFactory
from noyau.contexte import AutreEntreprise, activer_entreprise, activer_utilisateur
from noyau.models import Validation

pytestmark = pytest.mark.django_db
R = Role
DEMAIN = datetime.date.today() + datetime.timedelta(days=1)


@pytest.fixture
def entreprise():
    return EntrepriseFactory()


@pytest.fixture
def personnes(entreprise):
    return {role: UtilisateurFactory(entreprise=entreprise, roles=[role]) for role in R}


@pytest.fixture
def en_tant_que(entreprise, personnes):
    @contextmanager
    def activer(role):
        with activer_entreprise(entreprise), activer_utilisateur(personnes[role]):
            yield personnes[role]

    return activer


@pytest.fixture
def catalogue(entreprise):
    return {
        "baguette": ProduitFactory(entreprise=entreprise, nom="Pain baguette", prix_reference=250),
        "croissant": ProduitFactory(entreprise=entreprise, nom="Croissant", prix_reference=300),
        "farine": ProduitFactory(
            entreprise=entreprise, nom="Farine", unite=Unite.KG, prix_reference=900
        ),
    }


@pytest.fixture
def client_baobab(entreprise):
    adresse = AdresseFactory(
        client__entreprise=entreprise, client__nom="Le Baobab", par_defaut=True
    )
    return adresse.client


def nouvelle(client, catalogue, **champs):
    donnees = {
        "client": client,
        "lignes": [(catalogue["baguette"], 120), (catalogue["croissant"], 30)],
        "canal": Canal.WHATSAPP,
        "date_livraison": DEMAIN,
        "creneau": "06h–08h",
        **champs,
    }
    return services.creer_commande(**donnees)


@pytest.fixture
def confirmee(en_tant_que, client_baobab, catalogue):
    with en_tant_que(R.COMMERCIAL):
        commande = nouvelle(client_baobab, catalogue)
        services.soumettre_commande(commande)
        return services.confirmer_commande(commande)


def lignes(commande):
    return {
        ligne.produit.nom: (ligne.quantite, ligne.prix_unitaire)
        for ligne in LigneCommande.toutes_entreprises.filter(commande=commande, retiree=False)
    }


# --- Création -----------------------------------------------------------------


def test_creation_numero_auteur_prix_figes(en_tant_que, client_baobab, catalogue):
    with en_tant_que(R.COMMERCIAL) as commercial:
        commande = nouvelle(client_baobab, catalogue)
        assert commande.numero == f"CMD-{datetime.date.today().year}-000001"
        assert (commande.statut, commande.cree_par) == (Statut.BROUILLON, commercial)
        assert commande.adresse == client_baobab.adresse_par_defaut
        assert lignes(commande) == {"Pain baguette": (120, 250), "Croissant": (30, 300)}
        assert commande.montant_total == 39000


@pytest.mark.parametrize("role", [R.FACTURATION, R.CHAUFFEUR, R.DIRECTION])
def test_creation_refusee_au_mauvais_role(en_tant_que, client_baobab, catalogue, role):
    with en_tant_que(role), pytest.raises(PermissionDenied):
        nouvelle(client_baobab, catalogue)


@pytest.mark.parametrize(
    ("champs", "message"),
    [
        ({"creneau": "23h–01h"}, "Créneau inconnu"),
        ({"lignes": [("baguette", Decimal("1.5"))]}, "nombre entier"),
        ({"lignes": [("baguette", 1), ("baguette", 2)]}, "apparaît deux fois"),
        ({"canal": ""}, "canal"),
    ],
)
def test_creation_refusee_si_invalide_sans_consommer_de_numero(
    en_tant_que, client_baobab, catalogue, champs, message
):
    if "lignes" in champs:
        champs["lignes"] = [(catalogue[nom], q) for nom, q in champs["lignes"]]
    with en_tant_que(R.COMMERCIAL):
        with pytest.raises(ValidationError, match=message):
            nouvelle(client_baobab, catalogue, **champs)
        assert not Commande.objects.exists()
        assert nouvelle(client_baobab, catalogue).numero.endswith("-000001")


def test_creation_refusee_pour_client_ou_produit_inactif(en_tant_que, client_baobab, catalogue):
    catalogue["croissant"].actif = False
    catalogue["croissant"].save()
    with en_tant_que(R.COMMERCIAL):
        with pytest.raises(ValidationError, match="n'est plus au catalogue"):
            nouvelle(client_baobab, catalogue)
        client_baobab.actif = False
        client_baobab.save()
        with pytest.raises(ValidationError, match="désactivé"):
            nouvelle(client_baobab, catalogue, lignes=[(catalogue["baguette"], 1)])


def test_creneaux_de_l_entreprise(en_tant_que, entreprise, client_baobab, catalogue):
    Entreprise.objects.filter(pk=entreprise.pk).update(options={"creneaux": ["05h–07h"]})
    entreprise.refresh_from_db()
    with en_tant_que(R.COMMERCIAL), pytest.raises(ValidationError, match="05h–07h"):
        nouvelle(client_baobab, catalogue)


# --- Modification avant confirmation ----------------------------------------------


def test_modification_libre_avant_confirmation(en_tant_que, client_baobab, catalogue):
    with en_tant_que(R.COMMERCIAL):
        commande = nouvelle(client_baobab, catalogue)
        catalogue["baguette"].prix_reference = 275
        catalogue["baguette"].save()
        services.modifier_commande(
            commande,
            lignes=[(catalogue["baguette"], 100), (catalogue["farine"], Decimal("2.5"))],
            instructions="Entrée de service",
        )
        assert lignes(commande) == {
            "Pain baguette": (100, 250),  # prix figé à la création, conservé
            "Farine": (Decimal("2.5"), 900),
        }
        assert LigneCommande.objects.get(commande=commande, produit=catalogue["croissant"]).retiree
        commande.refresh_from_db()
        assert commande.instructions == "Entrée de service"


def test_modification_directe_refusee_apres_confirmation(en_tant_que, confirmee, catalogue):
    with en_tant_que(R.GESTIONNAIRE), pytest.raises(services.ActionImpossible, match="demander"):
        services.modifier_commande(confirmee, lignes=[(catalogue["baguette"], 1)])


# --- Soumission et confirmation -------------------------------------------------


def test_parcours_brouillon_a_confirmee(en_tant_que, client_baobab, catalogue):
    with en_tant_que(R.COMMERCIAL):
        commande = nouvelle(client_baobab, catalogue)
        services.soumettre_commande(commande)
        commande = services.confirmer_commande(commande, note="OK par WhatsApp")
    assert (commande.statut, commande.note_confirmation) == (Statut.CONFIRMEE, "OK par WhatsApp")


def test_soumission_d_une_commande_incomplete(en_tant_que, client_baobab, catalogue):
    with en_tant_que(R.COMMERCIAL):
        commande = nouvelle(client_baobab, catalogue, lignes=[])
        with pytest.raises(services.ActionImpossible, match="au moins un produit"):
            services.soumettre_commande(commande)


def test_confirmation_refusee_au_mauvais_role_et_statut(en_tant_que, client_baobab, catalogue):
    with en_tant_que(R.COMMERCIAL):
        commande = nouvelle(client_baobab, catalogue)
        with pytest.raises(services.ActionImpossible, match="Brouillon"):
            services.confirmer_commande(commande)
        services.soumettre_commande(commande)
    with en_tant_que(R.FACTURATION), pytest.raises(PermissionDenied):
        services.confirmer_commande(commande)


def test_urgente_demande_de_validation_automatique(en_tant_que, client_baobab, catalogue):
    with en_tant_que(R.COMMERCIAL):
        commande = nouvelle(client_baobab, catalogue, urgente=True)
        services.soumettre_commande(commande)
        services.modifier_commande(commande, instructions="Vite")  # pas de seconde demande
        demandes = Validation.objects.filter(object_id=commande.pk, action="urgence")
        assert [d.decision for d in demandes] == [Validation.Decision.EN_ATTENTE]
        with pytest.raises(services.ActionImpossible, match="Urgence non encore validée"):
            services.confirmer_commande(commande)

    with en_tant_que(R.COMMERCIAL), pytest.raises(PermissionDenied):
        services.decider_validation_commande(demandes.get(), accepter=True)
    with en_tant_que(R.DIRECTION):
        services.decider_validation_commande(demandes.get(), accepter=True)
    with en_tant_que(R.COMMERCIAL):
        assert services.confirmer_commande(commande).statut == Statut.CONFIRMEE


# --- Annulation --------------------------------------------------------------------


def test_annulation_avant_confirmation(en_tant_que, client_baobab, catalogue):
    with en_tant_que(R.COMMERCIAL):
        commande = nouvelle(client_baobab, catalogue)
        with pytest.raises(services.ActionImpossible, match="motif"):
            services.annuler_commande(commande, "  ")
        commande = services.annuler_commande(commande, "Client a renoncé")
    assert (commande.statut, commande.motif_annulation) == (Statut.ANNULEE, "Client a renoncé")


def test_annulation_par_un_autre_commercial_refusee(
    en_tant_que, entreprise, client_baobab, catalogue
):
    with en_tant_que(R.COMMERCIAL):
        commande = nouvelle(client_baobab, catalogue)
    autre = UtilisateurFactory(entreprise=entreprise, roles=[R.COMMERCIAL])
    with (
        activer_entreprise(entreprise),
        activer_utilisateur(autre),
        pytest.raises(PermissionDenied),
    ):
        services.annuler_commande(commande, "Erreur")


def test_annulation_apres_confirmation_par_demande(en_tant_que, confirmee):
    with en_tant_que(R.COMMERCIAL):
        with pytest.raises(services.ActionImpossible, match="demander l'annulation"):
            services.annuler_commande(confirmee, "Fermeture")
        demande = services.demander_annulation(confirmee, "Fermeture exceptionnelle")
        with pytest.raises(services.ActionImpossible, match="déjà en attente"):
            services.demander_annulation(confirmee, "Encore")

    with en_tant_que(R.GESTIONNAIRE):
        services.decider_validation_commande(demande, accepter=True)
    confirmee.refresh_from_db()
    assert (confirmee.statut, confirmee.motif_annulation) == (
        Statut.ANNULEE,
        "Fermeture exceptionnelle",
    )


def test_annulation_refusee_par_le_responsable(en_tant_que, confirmee):
    with en_tant_que(R.COMMERCIAL):
        demande = services.demander_annulation(confirmee, "Fermeture")
    with en_tant_que(R.DIRECTION):
        services.decider_validation_commande(demande, accepter=False)
    confirmee.refresh_from_db()
    assert confirmee.statut == Statut.CONFIRMEE


# --- Modification après confirmation --------------------------------------------


def test_modification_demandee_puis_appliquee(en_tant_que, confirmee, catalogue):
    nouvelle_date = DEMAIN + datetime.timedelta(days=1)
    with en_tant_que(R.COMMERCIAL):
        demande = services.demander_modification(
            confirmee,
            "demande client par WhatsApp",
            lignes=[(catalogue["baguette"], 120), (catalogue["croissant"], 40)],
            date_livraison=nouvelle_date,
        )
        assert lignes(confirmee)["Croissant"] == (30, 300)  # rien n'est appliqué avant décision

    with en_tant_que(R.DIRECTION) as direction:
        services.decider_validation_commande(demande, accepter=True)
    confirmee.refresh_from_db()
    assert lignes(confirmee)["Croissant"] == (40, 300)
    assert confirmee.date_livraison == nouvelle_date
    assert confirmee.statut == Statut.CONFIRMEE
    demande.refresh_from_db()
    assert (demande.decision, demande.validee_par) == (Validation.Decision.ACCEPTEE, direction)


def test_echec_de_l_application_annule_la_decision(en_tant_que, confirmee, catalogue):
    with en_tant_que(R.COMMERCIAL):
        demande = services.demander_modification(
            confirmee,
            "Ajout",
            lignes=[
                (catalogue["baguette"], 120),
                (catalogue["croissant"], 30),
                (catalogue["farine"], 1),
            ],
        )
    catalogue["farine"].actif = False
    catalogue["farine"].save()
    with en_tant_que(R.GESTIONNAIRE), pytest.raises(ValidationError):
        services.decider_validation_commande(demande, accepter=True)
    demande.refresh_from_db()
    assert demande.decision == Validation.Decision.EN_ATTENTE
    assert "Farine" not in lignes(confirmee)


def test_demande_de_modification_avant_confirmation_refusee(en_tant_que, client_baobab, catalogue):
    with en_tant_que(R.COMMERCIAL):
        commande = nouvelle(client_baobab, catalogue)
        with pytest.raises(services.ActionImpossible, match="la modifier directement"):
            services.demander_modification(commande, "x", instructions="y")


# --- Isolation ----------------------------------------------------------------------


def test_commande_d_une_autre_entreprise_inaccessible(en_tant_que, confirmee):
    with activer_entreprise(None):
        etranger = UtilisateurFactory(entreprise=EntrepriseFactory(), roles=[R.GESTIONNAIRE])
    with activer_entreprise(etranger.entreprise), activer_utilisateur(etranger):
        with pytest.raises(Commande.DoesNotExist):
            services.demander_annulation(confirmee, "Intrusion")


def test_client_ou_produit_d_une_autre_entreprise_refuse(en_tant_que, client_baobab, catalogue):
    with activer_entreprise(None):
        ailleurs = EntrepriseFactory()
        adresse_etrangere = AdresseFactory(client__entreprise=ailleurs, par_defaut=True)
        produit_etranger = ProduitFactory(entreprise=ailleurs)
    with en_tant_que(R.COMMERCIAL):
        with pytest.raises(AutreEntreprise):
            nouvelle(adresse_etrangere.client, catalogue)
        with pytest.raises(AutreEntreprise):
            nouvelle(client_baobab, catalogue, lignes=[(produit_etranger, 1)])
        with pytest.raises(AutreEntreprise):
            nouvelle(client_baobab, catalogue, adresse=adresse_etrangere)
    assert not Commande.toutes_entreprises.exists()
