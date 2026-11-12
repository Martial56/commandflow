"""Notifications planifiées (Celery Beat) : résumé du jour, paiements en retard, récurrentes
non confirmées à l'heure limite, livraisons à facturer, tournées non parties et livraisons en
retard (§16.1 ; décisions 8.1, 8.5 et 8.6).

Lancée toutes les 15 minutes : chaque notification a une clé par jour, elle n'est donc
envoyée qu'une fois par destinataire.
"""

import datetime

from celery import shared_task
from django.urls import reverse
from django.utils import timezone

from comptes.models import Role
from entreprises.models import Entreprise
from entreprises.parametres import bornes_creneau, heure_limite_confirmation, heure_resume
from noyau.contexte import activer_entreprise
from noyau.templatetags.format_fr import fcfa

from .models import TypeNotification
from .services import notifier


def _pluriel(nombre, singulier, pluriel=None):
    return f"{nombre} {singulier if nombre <= 1 else (pluriel or singulier + 's')}"


def _production(date):
    """« production 5/6 produits complets » pour le plan de la date (8.6)."""
    from production.models import PlanProduction

    plan = PlanProduction.objects.filter(date_livraison=date, creneau="").first()
    besoins = list(plan.besoins.filter(quantite_a_produire__gt=0)) if plan else []
    if not besoins:
        return "aucune production prévue"
    complets = sum(1 for besoin in besoins if besoin.complet)
    return f"production {complets}/{len(besoins)} produits complets"


def _livraisons_de(veille):
    """« hier 40 livrées · 2 partielles · 1 en échec » (8.6)."""
    from logistique.models import Livraison, StatutLivraison

    statuts = list(
        Livraison.objects.filter(traitee_le__date=veille).values_list("statut", flat=True)
    )
    return (
        f"hier {statuts.count(StatutLivraison.LIVREE)} livrées · "
        f"{statuts.count(StatutLivraison.PARTIELLEMENT_LIVREE)} partielles · "
        f"{statuts.count(StatutLivraison.ECHOUEE)} en échec"
    )


def resume_du_jour(date):
    """« Résumé du jour : 96 commandes à livrer (5 à confirmer), production 5/6 produits
    complets, hier 40 livrées · 2 partielles · 1 en échec, 3 en anomalie… »."""
    from commandes.models import Commande, Statut
    from facturation.alertes import documents_manquants
    from facturation.services import livraisons_a_facturer
    from paiements.alertes import factures_en_retard

    du_jour = Commande.objects.filter(date_livraison=date).exclude(
        statut__in=[Statut.BROUILLON, Statut.ANNULEE]
    )
    a_confirmer = du_jour.filter(statut=Statut.A_CONFIRMER).count()
    anomalies = Commande.objects.filter(statut=Statut.EN_ANOMALIE).count()
    retards = list(factures_en_retard())
    morceaux = [
        f"{_pluriel(du_jour.count(), 'commande')} à livrer ({a_confirmer} à confirmer)",
        _production(date),
        _livraisons_de(date - datetime.timedelta(days=1)),
        f"{anomalies} en anomalie",
        f"{_pluriel(livraisons_a_facturer().count(), 'livraison')} à facturer",
        f"{_pluriel(documents_manquants().count(), 'document')} sans PDF",
        f"{_pluriel(len(retards), 'facture')} en retard "
        f"({fcfa(sum(f.solde for f in retards))} FCFA)",
    ]
    return "Résumé du jour : " + ", ".join(morceaux)


def _du_jour(date):
    notifier(
        TypeNotification.RESUME,
        resume_du_jour(date),
        roles=(Role.DIRECTION, Role.GESTIONNAIRE),
        lien=reverse("ecran_tableau_de_bord"),
        cle=f"resume-{date:%Y-%m-%d}",
        sauf_auteur=False,
    )
    from paiements.alertes import factures_en_retard

    retards = list(factures_en_retard())
    if retards:
        notifier(
            TypeNotification.PAIEMENT,
            f"{_pluriel(len(retards), 'facture')} en retard de paiement · "
            f"{fcfa(sum(f.solde for f in retards))} FCFA",
            roles=(Role.COMPTABILITE, Role.DIRECTION),
            lien=reverse("ecran_paiements"),
            cle=f"retards-{date:%Y-%m-%d}",
            sauf_auteur=False,
        )


def _recurrentes(date):
    """Récurrentes du lendemain encore à confirmer après l'heure limite (décision 8.1)."""
    from commandes.models import Commande, Statut

    demain = date + datetime.timedelta(days=1)
    nombre = Commande.objects.filter(
        date_livraison=demain, statut=Statut.A_CONFIRMER, modele_recurrent__isnull=False
    ).count()
    if nombre:
        notifier(
            TypeNotification.RECURRENTES,
            f"{_pluriel(nombre, 'commande récurrente', 'commandes récurrentes')} du "
            f"{demain:%d/%m} toujours à confirmer après l'heure limite",
            roles=(Role.COMMERCIAL, Role.GESTIONNAIRE),
            lien=reverse("ecran_recurrentes"),
            cle=f"recurrentes-{demain:%Y-%m-%d}",
            sauf_auteur=False,
        )


def _facturation(date):
    """Livraisons à facturer et documents sans PDF, pour le service Facturation (8.6)."""
    from facturation.alertes import documents_manquants
    from facturation.services import livraisons_a_facturer

    a_facturer = livraisons_a_facturer().count()
    manquants = documents_manquants().count()
    morceaux = []
    if a_facturer:
        morceaux.append(f"{_pluriel(a_facturer, 'livraison')} à facturer")
    if manquants:
        morceaux.append(f"{_pluriel(manquants, 'document')} sans PDF")
    if morceaux:
        notifier(
            TypeNotification.FACTURATION,
            ", ".join(morceaux),
            roles=(Role.FACTURATION,),
            lien=reverse("ecran_facturation"),
            cle=f"facturation-{date:%Y-%m-%d}",
            sauf_auteur=False,
        )


def _lien_tournees(date):
    return f"{reverse('ecran_tournees')}?date={date:%Y-%m-%d}"


def _departs(maintenant):
    """Tournée du jour pas encore partie au début de son premier créneau (8.6)."""
    from logistique.models import Chargement, StatutChargement, StatutTournee, Tournee

    tournees = Tournee.objects.filter(
        date=maintenant.date(),
        statut__in=[
            StatutTournee.PLANIFIEE,
            StatutTournee.EN_CHARGEMENT,
            StatutTournee.DEPART_AUTORISE,
        ],
    )
    for tournee in tournees:
        chargements = list(
            Chargement.objects.filter(tournee=tournee)
            .exclude(statut=StatutChargement.RETIRE)
            .select_related("commande")
        )
        creneaux = []
        for chargement in chargements:
            bornes = bornes_creneau(chargement.commande.creneau)
            if bornes:
                creneaux.append((bornes[0], chargement.commande.creneau))
        if not creneaux or maintenant.time() < min(creneaux)[0]:
            continue
        non_recues = sum(1 for c in chargements if c.statut != StatutChargement.RECUE_CHAUFFEUR)
        if non_recues:
            accord = "s" if non_recues > 1 else ""
            detail = f"{_pluriel(non_recues, 'commande')} non reçue{accord} par le livreur"
        else:
            detail = "toutes les commandes reçues, départ non déclaré"
        notifier(
            TypeNotification.LIVRAISON,
            f"{tournee.numero} ({tournee.livreur}) pas encore partie, créneau "
            f"{min(creneaux)[1]} commencé : {detail}",
            roles=(Role.GESTIONNAIRE,),
            lien=_lien_tournees(tournee.date),
            cle=f"depart-{tournee.pk}",
            sauf_auteur=False,
        )


def _retards_livraison(maintenant):
    """Livraison non traitée à la fin de son créneau (8.6)."""
    from logistique.models import Livraison, StatutLivraison, StatutTournee

    en_cours = Livraison.objects.filter(
        statut__in=[StatutLivraison.EN_ATTENTE, StatutLivraison.SUR_PLACE],
        chargement__tournee__statut=StatutTournee.EN_COURS,
    ).select_related("commande__client", "chargement__tournee")
    for livraison in en_cours:
        tournee = livraison.chargement.tournee
        commande = livraison.commande
        bornes = bornes_creneau(commande.creneau)
        en_retard = tournee.date < maintenant.date() or (
            tournee.date == maintenant.date()
            and bornes is not None
            and maintenant.time() >= bornes[1]
        )
        if not en_retard:
            continue
        notifier(
            TypeNotification.LIVRAISON,
            f"Livraison en retard · {commande.numero} · {commande.client.nom} : créneau "
            f"{commande.creneau} dépassé ({tournee.numero}, {tournee.livreur})",
            roles=(Role.GESTIONNAIRE,),
            lien=_lien_tournees(tournee.date),
            cle=f"retard-livraison-{livraison.pk}",
            sauf_auteur=False,
        )


@shared_task
def notifications_planifiees(maintenant=None):
    """`maintenant` (ISO) sert aux tests."""
    maintenant = datetime.datetime.fromisoformat(maintenant) if maintenant else timezone.localtime()
    traitees = []
    for entreprise in Entreprise.objects.all():
        with activer_entreprise(entreprise):
            if maintenant.time() >= heure_resume(entreprise):
                _du_jour(maintenant.date())
                _facturation(maintenant.date())
            _departs(maintenant)
            _retards_livraison(maintenant)
            if maintenant.time() >= heure_limite_confirmation(entreprise):
                _recurrentes(maintenant.date())
        traitees.append(entreprise.slug)
    return traitees
