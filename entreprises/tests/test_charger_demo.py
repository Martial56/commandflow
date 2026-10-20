import pytest
from django.core.management import CommandError, call_command
from django.urls import reverse

from comptes.models import Role, Utilisateur
from entreprises.management.commands.charger_demo import MOT_DE_PASSE_DEMO
from entreprises.models import Entreprise

pytestmark = pytest.mark.django_db


@pytest.fixture
def mode_developpement(settings):
    settings.DEBUG = True


def test_cree_les_deux_entreprises_et_un_compte_par_role(mode_developpement):
    call_command("charger_demo")
    mie_dore = Entreprise.objects.get(slug="mie-dore")
    assert Entreprise.objects.filter(slug="boulangerie-demo").exists()

    comptes = Utilisateur.objects.filter(entreprise=mie_dore)
    assert {u.role_principal for u in comptes} == set(Role.values)
    for utilisateur in comptes:
        groupes = list(utilisateur.groups.values_list("name", flat=True))
        assert groupes == [Role(utilisateur.role_principal).label]


def test_relancable_sans_doublon(mode_developpement):
    call_command("charger_demo")
    nombre = (Entreprise.objects.count(), Utilisateur.objects.count())
    awa = Utilisateur.objects.get(username="awa.d")
    awa.set_password("mot-de-passe-change")
    awa.save()

    call_command("charger_demo")
    assert (Entreprise.objects.count(), Utilisateur.objects.count()) == nombre
    # Une relance n'écrase pas un mot de passe changé.
    awa.refresh_from_db()
    assert awa.check_password("mot-de-passe-change")


def test_permission_gestion_des_utilisateurs(mode_developpement):
    call_command("charger_demo")
    assert Utilisateur.objects.get(username="ibrahim.t").has_perm("comptes.gerer_utilisateurs")
    assert not Utilisateur.objects.get(username="aminata.k").has_perm("comptes.gerer_utilisateurs")


def test_connexion_avec_un_compte_de_demonstration(client, mode_developpement):
    call_command("charger_demo")
    reponse = client.post(
        reverse("account_login"), {"login": "+225 07 00 00 00 05", "password": MOT_DE_PASSE_DEMO}
    )
    assert reponse.status_code == 302
    assert client.get(reverse("accueil")).url == reverse("ecran_chargement")


def test_refusee_hors_developpement(settings):
    settings.DEBUG = False
    with pytest.raises(CommandError):
        call_command("charger_demo")
    assert not Entreprise.objects.exists()


def test_donnees_metier_de_mie_dore(mode_developpement):
    from catalogue.models import Produit
    from clients.models import Client
    from commandes.models import Commande, ModeleRecurrent, Statut

    call_command("charger_demo")
    mie_dore = Entreprise.objects.get(slug="mie-dore")
    clients = Client.toutes_entreprises.filter(entreprise=mie_dore)
    assert {"Restaurant Le Baobab", "Supérette Les Palmiers"} <= set(
        clients.values_list("nom", flat=True)
    )
    assert Produit.toutes_entreprises.filter(entreprise=mie_dore).count() == 4
    assert ModeleRecurrent.toutes_entreprises.filter(entreprise=mie_dore).count() == 6
    commandes = Commande.toutes_entreprises.filter(entreprise=mie_dore)
    statuts = set(commandes.values_list("statut", flat=True))
    assert {Statut.BROUILLON, Statut.A_CONFIRMER, Statut.A_PLANIFIER} <= statuts
    assert commandes.filter(urgente=True).exists()
    assert not Commande.toutes_entreprises.exclude(entreprise=mie_dore).exists()

    nombre = commandes.count()
    call_command("charger_demo")
    assert Commande.toutes_entreprises.filter(entreprise=mie_dore).count() == nombre
    assert Client.toutes_entreprises.filter(entreprise=mie_dore).count() == clients.count()
