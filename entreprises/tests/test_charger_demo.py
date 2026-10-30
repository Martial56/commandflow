import datetime

import pytest
from django.core.management import CommandError, call_command
from django.urls import reverse
from django.utils import timezone

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

    # Sprint 3 : plan du lendemain en brouillon, alimenté par les récurrentes confirmées.
    from production.models import PlanProduction, StatutPlan

    demain = timezone.localdate() + datetime.timedelta(days=1)
    plan = PlanProduction.toutes_entreprises.get(entreprise=mie_dore, date_livraison=demain)
    assert plan.statut == StatutPlan.BROUILLON
    assert commandes.filter(statut=Statut.A_CONFIRMER, modele_recurrent__isnull=False).exists()

    nombre = commandes.count()
    call_command("charger_demo")
    assert Commande.toutes_entreprises.filter(entreprise=mie_dore).count() == nombre
    assert Client.toutes_entreprises.filter(entreprise=mie_dore).count() == clients.count()


def test_tournees_du_jour(mode_developpement):
    """Sprint 4 : tournées du jour à différents stades (maquettes 3, 4 et 14)."""
    from logistique.models import Chargement, Prestataire, StatutChargement, Tournee, Vehicule
    from noyau.models import Validation

    call_command("charger_demo")
    mie_dore = Entreprise.objects.get(slug="mie-dore")
    tournees = Tournee.toutes_entreprises.filter(
        entreprise=mie_dore, date=timezone.localdate()
    ).order_by("numero")
    assert [t.livreur.__str__() for t in tournees] == ["Konan K.", "Express Livr."]
    assert Prestataire.toutes_entreprises.get(nom="Express Livr.").compte.username == "express.livr"
    assert Vehicule.toutes_entreprises.filter(entreprise=mie_dore).count() == 2
    statuts = sorted(
        Chargement.toutes_entreprises.filter(tournee__in=tournees).values_list("statut", flat=True)
    )
    assert statuts == sorted(
        [
            StatutChargement.A_PREPARER,
            StatutChargement.ECART_CONSTATE,
            StatutChargement.RECUE_CHAUFFEUR,
            StatutChargement.VERIFIEE_PREPARATION,
        ]
    )
    assert Validation.toutes_entreprises.filter(
        entreprise=mie_dore, decision=Validation.Decision.EN_ATTENTE, contenu__objet="ecart"
    ).exists()

    call_command("charger_demo")
    assert Tournee.toutes_entreprises.filter(entreprise=mie_dore).count() == 2


def test_pas_de_tournees_si_le_plan_du_jour_est_cloture(mode_developpement):
    """Relance sur une base où le plan du jour est clôturé : rien n'est créé pour ce jour."""
    from commandes.models import Commande
    from noyau.contexte import activer_entreprise
    from production.models import PlanProduction, StatutPlan

    call_command("charger_demo")
    mie_dore = Entreprise.objects.get(slug="mie-dore")
    with activer_entreprise(mie_dore):
        from logistique.models import Tournee

        aujourd_hui = timezone.localdate()
        # Plus de tournée ce jour (base de test seulement), et plan clôturé.
        Tournee.objects.filter(date=aujourd_hui).update(
            date=aujourd_hui - datetime.timedelta(days=7)
        )
        PlanProduction.objects.filter(date_livraison=aujourd_hui).update(statut=StatutPlan.CLOTURE)
        nombre = Commande.objects.filter(date_livraison=aujourd_hui).count()
    call_command("charger_demo")
    with activer_entreprise(mie_dore):
        assert Commande.objects.filter(date_livraison=aujourd_hui).count() == nombre
        assert not Tournee.objects.filter(date=aujourd_hui).exists()
