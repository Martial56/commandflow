"""Indicateurs du tableau de bord (maquette 8, §16.2 ; décisions 8.5).

Tout est calculé à la demande à partir des données des autres apps ; rien n'est stocké.
Le Commercial ne voit que les commandes qu'il a créées, sans créances (décision 8.5).
"""

from collections import Counter

from django.contrib.contenttypes.models import ContentType
from django.db.models import Count, Q

from commandes.models import Commande, Statut
from logistique.models import Livraison, StatutTournee, Tournee
from logistique.services import arrets_traites
from noyau.models import Validation

# Barres « Avancement des commandes du jour » (maquette 8) : libellé et statuts regroupés.
AVANCEMENT = [
    ("À confirmer", [Statut.A_CONFIRMER]),
    (
        "En préparation",
        [Statut.CONFIRMEE, Statut.A_PLANIFIER, Statut.EN_PREPARATION, Statut.PRETE_CHARGEMENT],
    ),
    ("En livraison", [Statut.EN_LIVRAISON]),
    ("Livrées", [Statut.LIVREE, Statut.PARTIELLEMENT_LIVREE]),
    ("En anomalie", [Statut.EN_ANOMALIE]),
]
# Commandes « du jour » : à livrer ce jour, ni brouillon ni annulée.
EXCLUES_DU_JOUR = [Statut.BROUILLON, Statut.ANNULEE]
LIBELLES_DEMANDE = {
    Validation.Action.ANNULATION: ("demande d'annulation", "demandes d'annulation"),
    Validation.Action.MODIFICATION: ("demande de modification", "demandes de modification"),
    Validation.Action.URGENCE: ("demande d'urgence", "demandes d'urgence"),
    Validation.Action.EXCEPTION: ("demande d'exception", "demandes d'exception"),
}


def _commandes(utilisateur, seulement_les_siennes):
    commandes = Commande.objects.all()
    return commandes.filter(cree_par=utilisateur) if seulement_les_siennes else commandes


def commandes(date, utilisateur, seulement_les_siennes=False):
    """Cartes et barres d'avancement des commandes à livrer à cette date."""
    du_jour = (
        _commandes(utilisateur, seulement_les_siennes)
        .filter(date_livraison=date)
        .exclude(statut__in=EXCLUES_DU_JOUR)
    )
    chiffres = du_jour.aggregate(
        total=Count("pk"),
        recurrentes=Count("pk", filter=Q(modele_recurrent__isnull=False)),
        livrees=Count("pk", filter=Q(statut__in=[Statut.LIVREE, Statut.PARTIELLEMENT_LIVREE])),
        partielles=Count("pk", filter=Q(statut=Statut.PARTIELLEMENT_LIVREE)),
    )
    par_statut = Counter(du_jour.values_list("statut", flat=True))
    total = chiffres["total"] or 0
    barres = []
    for libelle, statuts in AVANCEMENT:
        nombre = sum(par_statut[statut] for statut in statuts)
        barres.append(
            {
                "libelle": libelle,
                "nombre": nombre,
                "part": round(100 * nombre / total) if total else 0,
            }
        )
    # Les anomalies restent à traiter quelle que soit leur date de livraison.
    anomalies = _commandes(utilisateur, seulement_les_siennes).filter(statut=Statut.EN_ANOMALIE)
    return {**chiffres, "barres": barres, "anomalies": anomalies.count()}


def tournees_en_cours():
    """Tournées parties et non terminées, avec leurs arrêts traités sur le total."""
    resultat = []
    for tournee in (
        Tournee.objects.filter(statut=StatutTournee.EN_COURS)
        .select_related("chauffeur", "prestataire")
        .order_by("date", "numero")
    ):
        livraisons = Livraison.objects.filter(chargement__tournee=tournee).select_related(
            "commande__adresse"
        )
        communes = Counter(livraison.commande.adresse.commune for livraison in livraisons)
        tournee.total = len(livraisons)
        tournee.traites = len(arrets_traites(tournee))
        tournee.commune = communes.most_common(1)[0][0] if communes else ""
        resultat.append(tournee)
    return resultat


def creances(nombre=3):
    """Créances échues (total et nombre de clients) et les plus fortes, par client."""
    from paiements.alertes import factures_en_retard

    par_client = Counter()
    clients = {}
    for facture in factures_en_retard().select_related("client"):
        par_client[facture.client_id] += facture.solde
        clients[facture.client_id] = facture.client
    return {
        "total": sum(par_client.values()),
        "clients": len(par_client),
        "plus_fortes": [(clients[pk], montant) for pk, montant in par_client.most_common(nombre)],
    }


def alertes(utilisateur, seulement_les_siennes=False, limite=8):
    """« Alertes à traiter » (§16.1) : (type, texte, lien), les plus urgentes d'abord."""
    from django.urls import reverse

    resultat = []
    if utilisateur.has_perm("pilotage.voir_suivi"):
        from production.alertes import besoins_en_manque

        for besoin in besoins_en_manque().select_related("produit", "plan")[:3]:
            manque = besoin.quantite_a_produire - besoin.quantite_produite
            resultat.append(
                (
                    "Écart",
                    f"{besoin.produit.nom} : {manque.normalize():f} manquent pour la livraison "
                    f"du {besoin.plan.date_livraison:%d/%m}",
                    f"{reverse('ecran_production')}?date={besoin.plan.date_livraison:%Y-%m-%d}",
                )
            )
    for commande in (
        _commandes(utilisateur, seulement_les_siennes)
        .filter(statut=Statut.EN_ANOMALIE)
        .select_related("client")
        .order_by("date_livraison")[:3]
    ):
        livraison = commande.livraisons.order_by("-pk").first()
        motif = f" : {livraison.get_motif_echec_display().lower()}" if livraison else ""
        resultat.append(
            (
                "Échec",
                f"{commande.numero} · {commande.client.nom}{motif}",
                reverse("commande_detail", args=[commande.pk]),
            )
        )
    if utilisateur.has_perm("pilotage.voir_suivi"):
        demandes = Counter(
            Validation.objects.filter(
                content_type=ContentType.objects.get_for_model(Commande),
                decision=Validation.Decision.EN_ATTENTE,
            )
            .exclude(demandee_par=utilisateur)
            .values_list("action", flat=True)
        )
        for action, nombre in demandes.items():
            singulier, pluriel = LIBELLES_DEMANDE.get(action, ("demande", "demandes"))
            verbe = "attend" if nombre == 1 else "attendent"
            resultat.append(
                (
                    "Validation",
                    f"{nombre} {singulier if nombre == 1 else pluriel} {verbe} votre décision",
                    reverse("ecran_commandes"),
                )
            )
    if not seulement_les_siennes and utilisateur.has_perm("facturation.voir_factures"):
        from facturation.alertes import documents_manquants

        manquants = documents_manquants().count()
        if manquants:
            resultat.append(
                (
                    "Document",
                    f"{manquants} document{'s' if manquants > 1 else ''} sans PDF joint",
                    f"{reverse('ecran_facturation')}?vue=emis&etat=manquant",
                )
            )
    return resultat[:limite]
