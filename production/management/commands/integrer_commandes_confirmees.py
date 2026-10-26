"""Rattrapage : intègre au plan de production les commandes restées « Confirmée ».

Depuis le Sprint 3, une commande confirmée entre aussitôt dans le plan de sa date
(« À planifier »). Les commandes confirmées avant cette version sont rattrapées ici.
Relançable sans effet de bord : seules les commandes encore « Confirmée » sont traitées.

    python manage.py integrer_commandes_confirmees [--entreprise mie-dore]
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from commandes.models import Commande, Statut
from entreprises.models import Entreprise
from noyau.contexte import activer_entreprise
from production.services import integrer_commande


class Command(BaseCommand):
    help = "Intègre au plan de production les commandes confirmées avant le Sprint 3."

    def add_arguments(self, parser):
        parser.add_argument("--entreprise", help="Slug d'une seule entreprise (toutes sinon).")

    def handle(self, *args, entreprise=None, **options):
        entreprises = Entreprise.objects.all()
        if entreprise:
            entreprises = entreprises.filter(slug=entreprise)
            if not entreprises.exists():
                raise CommandError(f"Entreprise « {entreprise} » introuvable.")
        total = 0
        for societe in entreprises:
            with activer_entreprise(societe):
                for commande in Commande.objects.filter(statut=Statut.CONFIRMEE).order_by("pk"):
                    with transaction.atomic():
                        integrer_commande(commande)
                    total += 1
                    date = f"{commande.date_livraison:%d/%m}"
                    self.stdout.write(f"  {societe.nom} · {commande.numero} → plan du {date}")
        self.stdout.write(self.style.SUCCESS(f"{total} commande(s) intégrée(s) au plan."))
