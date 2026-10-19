"""Données de démonstration : Mie Doré (un compte par rôle) et une seconde entreprise.

Mie Doré reçoit aussi des données métier (produits, clients, modèles récurrents,
commandes) : voir `_donnees_metier.py`.

Relançable sans créer de doublons. Refusée hors développement (DEBUG=False), car les
comptes de démonstration partagent un mot de passe connu.
"""

from django.conf import settings
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from comptes.models import Role, Utilisateur
from entreprises.models import Entreprise
from noyau.contexte import activer_entreprise, activer_utilisateur

from . import _donnees_metier

MOT_DE_PASSE_DEMO = "demo-comandflow-2026"

ENTREPRISES = [
    {"slug": "mie-dore", "nom": "Mie Doré"},
    # Seconde entreprise : vérifier à la main que les données ne se mélangent pas.
    {"slug": "boulangerie-demo", "nom": "Boulangerie Démo"},
]

# (entreprise, identifiant, prénom, nom, téléphone, rôle). Noms repris des maquettes
# quand elles en donnent ; sinon noms fictifs.
COMPTES = [
    ("mie-dore", "ibrahim.t", "Ibrahim", "T.", "+2250700000001", Role.DIRECTION),
    ("mie-dore", "aminata.k", "Aminata", "K.", "+2250700000002", Role.GESTIONNAIRE),
    ("mie-dore", "fatou.b", "Fatou", "B.", "+2250700000003", Role.COMMERCIAL),
    ("mie-dore", "moussa.c", "Moussa", "C.", "+2250700000004", Role.PRODUCTION),
    ("mie-dore", "awa.d", "Awa", "D.", "+2250700000005", Role.PREPARATION),
    ("mie-dore", "konan.k", "Konan", "K.", "+2250700000006", Role.CHAUFFEUR),
    ("mie-dore", "yao.s", "Yao", "S.", "+2250700000007", Role.CHAUFFEUR),
    ("mie-dore", "mariam.o", "Mariam", "O.", "+2250700000008", Role.FACTURATION),
    ("mie-dore", "serge.a", "Serge", "A.", "+2250700000009", Role.COMPTABILITE),
    ("mie-dore", "express.livr", "Express", "Livr.", "+2250700000010", Role.PRESTATAIRE),
    ("boulangerie-demo", "gestion.demo", "Gestion", "Démo", "+2250700000101", Role.GESTIONNAIRE),
]  # fmt: skip

# Démonstration de la permission individuelle « gérer les utilisateurs ».
GESTION_UTILISATEURS = ["ibrahim.t"]


class Command(BaseCommand):
    help = "Charge les données de démonstration (Mie Doré et une seconde entreprise)."

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError(
                "charger_demo est réservé au développement (DEBUG=True) : "
                "les comptes de démonstration ont un mot de passe connu."
            )
        with transaction.atomic():
            entreprises = self._charger_entreprises()
            crees = self._charger_comptes(entreprises)
            gestionnaire = Utilisateur.objects.get(username="aminata.k")
            with activer_entreprise(entreprises["mie-dore"]), activer_utilisateur(gestionnaire):
                _donnees_metier.charger(self.stdout)

        self.stdout.write(
            self.style.SUCCESS(f"{crees} compte(s) créé(s), {len(COMPTES)} au total.")
        )
        self.stdout.write(f"Mot de passe de tous les comptes : {MOT_DE_PASSE_DEMO}")
        for slug, _identifiant, prenom, nom, telephone, role in COMPTES:
            entreprise, personne = entreprises[slug].nom, f"{prenom} {nom}"
            self.stdout.write(f"  {entreprise:<17} {role.label:<13} {personne:<14} {telephone}")
        self.stdout.write(
            "Direction et Comptabilité : activation du code de vérification à la 1re connexion."
        )

    def _charger_entreprises(self):
        entreprises = {}
        for donnees in ENTREPRISES:
            entreprise, _ = Entreprise.objects.update_or_create(
                slug=donnees["slug"], defaults={"nom": donnees["nom"]}
            )
            entreprises[entreprise.slug] = entreprise
        return entreprises

    def _charger_comptes(self, entreprises):
        permission = Permission.objects.get(
            content_type__app_label="comptes", codename="gerer_utilisateurs"
        )
        crees = 0
        for slug, identifiant, prenom, nom, telephone, role in COMPTES:
            utilisateur, cree = Utilisateur.objects.update_or_create(
                username=identifiant,
                defaults={
                    "entreprise": entreprises[slug],
                    "first_name": prenom,
                    "last_name": nom,
                    "telephone": telephone,
                    "email": f"{identifiant}@{slug}.example",
                    "role_principal": role,
                    "is_active": True,
                },
            )
            if cree:
                # Mot de passe fixé à la création seulement : une relance ne l'écrase pas.
                utilisateur.set_password(MOT_DE_PASSE_DEMO)
                utilisateur.save(update_fields=["password"])
                crees += 1
            utilisateur.groups.set([Group.objects.get(name=Role(role).label)])
            if identifiant in GESTION_UTILISATEURS:
                utilisateur.user_permissions.add(permission)
        return crees
