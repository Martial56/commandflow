"""Données de démonstration des Sprints 4 et 5 pour Mie Doré : véhicules, prestataire,
tournées du jour à différents stades du chargement (maquettes 3, 4 et 14), et une tournée
en cours de livraison (maquettes 5 et 6) avec un arrêt livré, un partiel, un échec, un
retour et un incident.

Tout passe par les services, sous l'identité de chaque acteur (gestionnaire, préparatrice,
chauffeur). Relançable : les tournées du jour ne sont créées que s'il n'y en a aucune.
"""

from django.utils import timezone

from commandes import services as commandes
from commandes.models import Canal
from comptes.models import Utilisateur
from logistique import services as logistique
from logistique.models import (
    EtatRetour,
    Livraison,
    MotifEcart,
    MotifEchec,
    MoyenAccord,
    Prestataire,
    Tournee,
    TypeIncident,
    Vehicule,
)
from logistique.services import Resultat
from noyau.contexte import activer_utilisateur
from production import services as production
from production.models import PlanProduction, StatutPlan

VEHICULES = [("AB-1234-CI", "Camionnette"), ("AB-5678-CI", "Tricycle")]

# Commandes du jour : (client, créneau, {code produit: quantité}).
COMMANDES_DU_JOUR = [
    ("Hôtel Ivoire Lagune", "06h–08h", {"BAG": 80, "CRO": 40}),
    ("Restaurant Le Baobab", "06h–08h", {"BAG": 120, "CRO": 40, "MIE": 15}),
    ("Supérette Les Palmiers", "08h–10h", {"BAG": 100, "CRO": 60, "MIE": 20}),
    ("Supermarché Prosuma Riviera", "08h–10h", {"BAG": 150, "MIE": 40}),
    ("Boutique Chez Fatou", "08h–10h", {"BAG": 40}),
    # Tournée en cours de Yao S. (Sprint 5).
    ("Café Le Plateau", "06h–08h", {"BAG": 60, "CRO": 30}),
    ("Traiteur Saveurs d'Akwaba", "08h–10h", {"BAG": 80, "CHO": 40}),
    ("Supermarché Prosuma Riviera", "10h–12h", {"BAG": 100}),
    ("Restaurant Le Baobab", "16h–18h", {"BAG": 50}),
]


def charger(fiches, produits, sortie):
    vehicules = _vehicules()
    prestataire = _prestataire()
    jour = timezone.localdate()
    if Tournee.objects.filter(date=jour).exists():
        return
    plan = PlanProduction.objects.filter(date_livraison=jour, creneau="").first()
    if plan is not None and plan.statut not in (StatutPlan.BROUILLON, StatutPlan.VALIDE):
        sortie.write("Tournées de démonstration non créées : le plan du jour est clôturé.")
        return
    pretes = _commandes_pretes(fiches, produits, jour)

    personne = {u.username: u for u in Utilisateur.objects.filter(entreprise__slug="mie-dore")}
    hotel, baobab, palmiers, prosuma, _fatou = pretes[:5]  # Chez Fatou reste « à affecter »
    konan = logistique.creer_tournee(
        date=jour, chauffeur=personne["konan.k"], vehicule=vehicules[0]
    )
    express = logistique.creer_tournee(date=jour, prestataire=prestataire, vehicule=vehicules[1])
    recue, verifiee, en_ecart = (
        logistique.affecter_commande(konan, commande) for commande in (hotel, baobab, palmiers)
    )
    logistique.affecter_commande(express, prosuma)

    with activer_utilisateur(personne["awa.d"]):
        for chargement in (recue, verifiee):
            for ligne in chargement.lignes.all():
                logistique.compter_ligne(ligne, ligne.quantite_attendue)
            logistique.valider_preparation(chargement)
        # Écart de la maquette 3 : 30 croissants manquants, signalé au gestionnaire.
        for ligne in en_ecart.lignes.select_related("ligne_commande__produit"):
            manque = 30 if ligne.ligne_commande.produit.code == "CRO" else 0
            logistique.compter_ligne(
                ligne, ligne.quantite_attendue - manque, motif=MotifEcart.PRODUCTION_INSUFFISANTE
            )
        logistique.signaler_ecart(en_ecart, "Fournée de croissants insuffisante")
    with activer_utilisateur(personne["konan.k"]):
        logistique.confirmer_reception(
            recue, {ligne: ligne.quantite_preparee for ligne in recue.lignes.all()}
        )
    en_cours, livree = _tournee_en_cours(jour, pretes[5:], personne, produits)
    sortie.write(
        f"Tournées du {jour:%d/%m} : {konan} (Konan K.) et {express} (prestataire), "
        f"une commande à affecter, un écart à décider ; {en_cours} (Yao S.) en livraison."
    )
    # Sprint 6 : facturation et paiements à partir des livraisons de Yao S.
    from . import _donnees_facturation

    _donnees_facturation.charger(livree, jour, fiches, personne, sortie)


def _tournee_en_cours(jour, pretes, personne, produits):
    """Tournée de Yao S. partie : arrêt livré (avec un retour), partiel (reliquat), en
    échec, et un dernier à faire ; un incident signalé (maquettes 5, 6 et 14)."""
    tournee = logistique.creer_tournee(date=jour, chauffeur=personne["yao.s"])
    chargements = [logistique.affecter_commande(tournee, commande) for commande in pretes]
    with activer_utilisateur(personne["awa.d"]):
        for chargement in chargements:
            for ligne in chargement.lignes.all():
                logistique.compter_ligne(ligne, ligne.quantite_attendue)
            logistique.valider_preparation(chargement)
    with activer_utilisateur(personne["yao.s"]):
        for chargement in chargements:
            logistique.confirmer_reception(
                chargement, {ligne: ligne.quantite_preparee for ligne in chargement.lignes.all()}
            )
        logistique.demarrer_tournee(tournee)
        livree, partielle, echec, _a_faire = (
            Livraison.objects.get(chargement=chargement) for chargement in chargements
        )
        logistique.arriver_chez_client(livree, latitude="5.3197", longitude="-4.0168")
        logistique.cloturer_livraison(
            livree,
            Resultat.LIVREE,
            quantites={ligne: ligne.quantite_prevue for ligne in livree.lignes.all()},
            receptionnaire_nom="Mme Touré",
            numero_bordereau="BL-0412",
            latitude="5.3197",
            longitude="-4.0168",
        )
        logistique.declarer_retour(
            livree,
            produit=produits["CRO"],
            quantite=5,
            motif="Croissants écrasés dans le bac",
            etat=EtatRetour.ABIME,
        )
        logistique.arriver_chez_client(partielle)
        logistique.cloturer_livraison(
            partielle,
            Resultat.PARTIELLE,
            quantites={
                ligne: 25 if ligne.ligne_commande.produit.code == "CHO" else ligne.quantite_prevue
                for ligne in partielle.lignes.select_related("ligne_commande__produit")
            },
            motif=MotifEchec.MARCHANDISE_MANQUANTE,
            accord={"nom_client": "M. Kouadio", "moyen": MoyenAccord.ORAL},
            receptionnaire_nom="M. Kouadio",
        )
        logistique.cloturer_livraison(
            echec,
            Resultat.ECHEC,
            motif=MotifEchec.CLIENT_ABSENT,
            commentaire="Magasin fermé à l'arrivée",
        )
        logistique.signaler_incident(
            tournee, TypeIncident.PANNE, "Crevaison à Marcory, 20 minutes de retard"
        )
    return tournee, livree


def _vehicules():
    return [
        Vehicule.objects.get_or_create(immatriculation=immatriculation, defaults={"libelle": nom})[
            0
        ]
        for immatriculation, nom in VEHICULES
    ]


def _prestataire():
    compte = Utilisateur.objects.filter(username="express.livr").first()
    return Prestataire.objects.get_or_create(
        nom="Express Livr.", defaults={"telephone": "07 00 00 00 10", "compte": compte}
    )[0]


def _commandes_pretes(fiches, produits, jour):
    """Commandes du jour confirmées, produites et prêtes au chargement (Sprint 3).
    Le plan du jour est en brouillon ou validé (vérifié par l'appelant)."""
    creees = []
    for client, creneau, lignes in COMMANDES_DU_JOUR:
        commande = commandes.creer_commande(
            client=fiches[client],
            lignes=[(produits[code], quantite) for code, quantite in lignes.items()],
            canal=Canal.WHATSAPP,
            date_livraison=jour,
            creneau=creneau,
        )
        commandes.soumettre_commande(commande)
        creees.append(commandes.confirmer_commande(commande, note="Démonstration du chargement"))
    plan = PlanProduction.objects.get(date_livraison=jour, creneau="")
    if plan.statut == StatutPlan.BROUILLON:
        production.valider_plan(plan)
    for besoin in plan.besoins.all():
        manque = besoin.quantite_a_produire - besoin.quantite_produite
        if manque > 0:
            production.enregistrer_saisie(besoin, manque, lot="DEMO")
    production.marquer_plan_pret(plan)
    for commande in creees:
        commande.refresh_from_db()
    return [c for c in creees if c.statut == "prete_chargement"]
