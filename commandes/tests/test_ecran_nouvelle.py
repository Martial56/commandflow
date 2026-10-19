import datetime
import html

import pytest
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from catalogue.models import Unite
from catalogue.tests.factories import ProduitFactory
from clients.tests.factories import AdresseFactory
from commandes.models import Commande, LigneCommande, Statut
from comptes.models import Role
from comptes.tests.factories import UtilisateurFactory
from entreprises.tests.factories import EntrepriseFactory
from noyau.models import PieceJointe, Validation

pytestmark = pytest.mark.django_db
NOUVELLE = reverse("commande_nouvelle")
DEMAIN = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()


@pytest.fixture(autouse=True)
def sans_2fa_obligatoire(settings):
    settings.MIDDLEWARE = [
        m for m in settings.MIDDLEWARE if "DoubleAuthentificationObligatoire" not in m
    ]


@pytest.fixture(autouse=True)
def stockage_temporaire(monkeypatch, tmp_path):
    champ = PieceJointe._meta.get_field("fichier")
    monkeypatch.setattr(champ, "storage", FileSystemStorage(location=tmp_path))


@pytest.fixture
def commercial(client):
    utilisateur = UtilisateurFactory(roles=[Role.COMMERCIAL])
    client.force_login(utilisateur)
    return utilisateur


@pytest.fixture
def donnees(commercial):
    entreprise = commercial.entreprise
    adresse = AdresseFactory(
        client__entreprise=entreprise, client__nom="Le Baobab", client__code="CLI-000042",
        par_defaut=True,
    )  # fmt: skip
    baguette = ProduitFactory(entreprise=entreprise, nom="Pain baguette", prix_reference=250)
    croissant = ProduitFactory(entreprise=entreprise, nom="Croissant", prix_reference=300)
    return {"adresse": adresse, "baguette": baguette, "croissant": croissant}


def saisie(donnees, **autres):
    return {
        "client": "Le Baobab — CLI-000042",
        "canal": "whatsapp",
        "adresse": donnees["adresse"].pk,
        "date_livraison": DEMAIN,
        "creneau": "06h–08h",
        "produit_1": donnees["baguette"].pk,
        "quantite_1": "120",
        "produit_2": donnees["croissant"].pk,
        "quantite_2": "40",
        "action": "brouillon",
        **autres,
    }


def test_ecran_conforme_a_la_maquette(client, commercial, donnees):
    contenu = client.get(NOUVELLE).content.decode()
    for texte in [
        "Nouvelle commande", "N° attribué à l'enregistrement", "Client et livraison",
        "Canal de réception", "Adresse de livraison", "Commande urgente", "Produits",
        "+ Ajouter un produit", "Prix unitaire (FCFA)", "Le prix est figé sur chaque ligne",
        "Instructions de livraison", "Bon de commande, photo ou capture WhatsApp",
        "Total commande", "Enregistrer le brouillon", "Soumettre pour confirmation",
        "Le Baobab — CLI-000042",
    ]:  # fmt: skip
        assert texte in contenu, texte


def test_client_preselectionne_depuis_sa_fiche(client, commercial, donnees):
    url = f"{NOUVELLE}?client={donnees['adresse'].client_id}"
    contenu = client.get(url).content.decode()
    assert 'value="Le Baobab — CLI-000042"' in contenu
    assert f'value="{donnees["adresse"].pk}" selected' in contenu


@pytest.mark.parametrize("role", [Role.FACTURATION, Role.CHAUFFEUR, Role.DIRECTION])
def test_ecran_refuse_aux_autres_roles(client, role):
    client.force_login(UtilisateurFactory(roles=[role]))
    assert client.get(NOUVELLE).status_code == 403
    assert client.post(reverse("commande_calcul")).status_code == 403


def test_enregistrer_le_brouillon(client, commercial, donnees):
    reponse = client.post(NOUVELLE, saisie(donnees))
    commande = Commande.toutes_entreprises.get()
    assert reponse.url == reverse("commande_detail", args=[commande.pk])
    assert (commande.statut, commande.cree_par) == (Statut.BROUILLON, commercial)
    lignes = LigneCommande.toutes_entreprises.filter(commande=commande).order_by("pk")
    assert [(ligne.quantite, ligne.prix_unitaire, ligne.montant) for ligne in lignes] == [
        (120, 250, 30000), (40, 300, 12000),
    ]  # fmt: skip
    contenu = client.get(reponse.url).content.decode()
    assert f"Brouillon {commande.numero} enregistré." in contenu
    assert "42 000" in contenu


def test_soumettre_pour_confirmation(client, commercial, donnees):
    client.post(NOUVELLE, saisie(donnees, action="soumettre"))
    assert Commande.toutes_entreprises.get().statut == Statut.A_CONFIRMER


def test_urgente_soumise_avec_demande_de_validation(client, commercial, donnees):
    reponse = client.post(NOUVELLE, saisie(donnees, action="soumettre", urgente="on"), follow=True)
    commande = Commande.toutes_entreprises.get()
    assert commande.urgente and commande.statut == Statut.A_CONFIRMER
    assert Validation.toutes_entreprises.filter(object_id=commande.pk, action="urgence").exists()
    contenu = html.unescape(reponse.content.decode())
    assert "Demande de validation de l'urgence envoyée." in contenu
    assert "URGENTE" in contenu


def test_erreurs_affichees_sans_rien_enregistrer(client, commercial, donnees):
    reponse = client.post(
        NOUVELLE, saisie(donnees, client="Inconnu", quantite_1="1,5", produit_2="", quantite_2="3")
    )
    contenu = reponse.content.decode()
    assert reponse.status_code == 200
    assert "Choisir un client actif dans la liste." in contenu
    assert "Quantité entière et positive attendue." in contenu
    assert "Choisir un produit." in contenu
    assert not Commande.toutes_entreprises.exists()


def test_quantite_decimale_au_kg(client, commercial, donnees):
    farine = ProduitFactory(
        entreprise=commercial.entreprise, nom="Farine", unite=Unite.KG, prix_reference=900
    )
    client.post(NOUVELLE, saisie(donnees, produit_2=farine.pk, quantite_2="2,5"))
    ligne = LigneCommande.toutes_entreprises.get(produit=farine)
    assert (ligne.quantite, ligne.montant) == (2.5, 2250)


def test_justificatif_photo(client, commercial, donnees):
    photo = SimpleUploadedFile("bon.jpg", b"\xff\xd8\xff photo", content_type="image/jpeg")
    reponse = client.post(NOUVELLE, saisie(donnees, justificatif=photo), follow=True)
    piece = PieceJointe.toutes_entreprises.get()
    assert (piece.type, piece.nom_original) == (PieceJointe.Type.PHOTO, "bon.jpg")
    assert "/fichiers/" in reponse.content.decode()


def test_justificatif_refuse_s_il_n_est_ni_image_ni_pdf(client, commercial, donnees):
    fichier = SimpleUploadedFile("macro.xlsm", b"PK", content_type="application/vnd.ms-excel")
    reponse = client.post(NOUVELLE, saisie(donnees, justificatif=fichier))
    assert "Joindre une image ou un PDF." in reponse.content.decode()
    assert not Commande.toutes_entreprises.exists()
    assert not PieceJointe.toutes_entreprises.exists()


def test_calcul_cote_serveur(client, commercial, donnees):
    reponse = client.post(
        reverse("commande_calcul"),
        {
            "produit_7": donnees["baguette"].pk,
            "quantite_7": "12",
            "produit_8": "",
            "quantite_8": "",
        },
    )
    contenu = reponse.content.decode()
    assert 'id="montant-7" hx-swap-oob="true"' in contenu
    assert "3 000" in contenu
    assert 'id="total" hx-swap-oob="true"' in contenu


def test_nouvelle_ligne_et_adresses_du_client(client, commercial, donnees):
    ligne = client.get(reverse("commande_ligne"), {"index": "5"}).content.decode()
    assert 'name="produit_5"' in ligne and "Pain baguette" in ligne
    adresses = client.get(reverse("commande_adresses"), {"client": "Le Baobab — CLI-000042"})
    assert donnees["adresse"].libelle in adresses.content.decode()


def test_clients_et_produits_d_une_autre_entreprise_invisibles(client, commercial, donnees):
    ailleurs = EntrepriseFactory()
    AdresseFactory(client__entreprise=ailleurs, client__nom="Client d'ailleurs")
    ProduitFactory(entreprise=ailleurs, nom="Produit d'ailleurs")
    contenu = client.get(NOUVELLE).content.decode()
    assert "ailleurs" not in contenu
    reponse = client.post(NOUVELLE, saisie(donnees, client="Client d'ailleurs"))
    assert "Choisir un client actif dans la liste." in reponse.content.decode()


def test_detail_d_une_commande_d_une_autre_entreprise_introuvable(client, commercial, donnees):
    from commandes.tests.factories import CommandeFactory

    autre = CommandeFactory(adresse__client__entreprise=EntrepriseFactory())
    assert client.get(reverse("commande_detail", args=[autre.pk])).status_code == 404
