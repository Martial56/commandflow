"""Base des commandes de gestion `importer_…` (options, contexte, rapport)."""

from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from entreprises.models import Entreprise

from .contexte import activer_entreprise, activer_utilisateur
from .importation import ecrire_modele, importer, lire_tableau


class CommandeImport(BaseCommand):
    """À compléter dans chaque app : `colonnes`, `obligatoires`, `exemple`, `traiter_ligne`."""

    colonnes = []
    obligatoires = []
    exemple = {}

    def add_arguments(self, parser):
        parser.add_argument("fichier", help="Fichier .xlsx ou .csv à importer.")
        parser.add_argument(
            "--entreprise", help="Identifiant (slug) de l'entreprise, par exemple mie-dore."
        )
        parser.add_argument(
            "--utilisateur", help="Identifiant de l'utilisateur à qui attribuer l'import."
        )
        parser.add_argument(
            "--simulation",
            action="store_true",
            help="Vérifie le fichier et affiche le rapport sans rien enregistrer.",
        )
        parser.add_argument(
            "--modele",
            action="store_true",
            help="Écrit un fichier Excel modèle (en-têtes et exemple) au lieu d'importer.",
        )

    def handle(self, *args, fichier, entreprise, utilisateur, simulation, modele, **options):
        if modele:
            ecrire_modele(fichier, self.colonnes, self.exemple)
            self.stdout.write(self.style.SUCCESS(f"Modèle écrit : {fichier}"))
            return
        if not entreprise:
            raise CommandError("Préciser l'entreprise : --entreprise <slug>.")
        try:
            entreprise = Entreprise.objects.get(slug=entreprise)
        except Entreprise.DoesNotExist:
            raise CommandError(f"Entreprise « {entreprise} » introuvable.") from None
        auteur = None
        if utilisateur:
            auteur = get_user_model().objects.filter(username=utilisateur, entreprise=entreprise)
            auteur = auteur.first()
            if auteur is None:
                raise CommandError(f"Utilisateur « {utilisateur} » introuvable dans {entreprise}.")
        if not Path(fichier).exists():
            raise CommandError(f"Fichier introuvable : {fichier}")

        try:
            lignes = lire_tableau(fichier)
        except ValueError as erreur:
            raise CommandError(str(erreur)) from None
        manquantes = [c for c in self.obligatoires if lignes and c not in lignes[0][1]]
        if manquantes:
            raise CommandError(f"Colonnes manquantes : {', '.join(manquantes)}.")

        with activer_entreprise(entreprise), activer_utilisateur(auteur):
            rapport = importer(lignes, self.traiter_ligne, simulation=simulation)

        prefixe = "Simulation (rien n'est enregistré) : " if simulation else ""
        self.stdout.write(self.style.SUCCESS(prefixe + rapport.resume()))
        for numero, message in rapport.rejets:
            self.stdout.write(self.style.WARNING(f"  Ligne {numero} rejetée : {message}"))
        return None

    def traiter_ligne(self, donnees):
        raise NotImplementedError
