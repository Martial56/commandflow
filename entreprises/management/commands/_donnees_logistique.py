"""Données de démonstration du Sprint 4 pour Mie Doré : véhicules, prestataire et tournées
du jour à différents stades du chargement (maquettes 3, 4 et 14).

Tout passe par les services, sous l'identité de chaque acteur (gestionnaire, préparatrice,
chauffeur). Relançable : les tournées du jour ne sont créées que s'il n'y en a aucune.
"""

from django.utils import timezone

from commandes import services as commandes
from commandes.models import Canal
from comptes.models import Utilisateur
from logistique import services as logistique
from logistique.models import MotifEcart, Prestataire, Tournee, Vehicule
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
    hotel, baobab, palmiers, prosuma, _fatou = pretes  # Chez Fatou reste « à affecter »
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
    sortie.write(
        f"Tournées du {jour:%d/%m} : {konan} (Konan K.) et {express} (prestataire), "
        "une commande à affecter, un écart à décider."
    )


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
