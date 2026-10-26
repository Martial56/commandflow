"""Données métier de démonstration pour Mie Doré : produits, clients, modèles récurrents,
commandes. Noms et prix repris des maquettes quand elles en donnent ; sinon fictifs.

Tout passe par les services (numérotation, prix figés, historique). Relançable sans
doublon : un produit est retrouvé par son code, un client par son nom, un modèle par
son client ; les commandes d'exemple ne sont créées que si l'entreprise n'en a aucune.
"""

import datetime

from django.utils import timezone

from catalogue import services as catalogue
from catalogue.models import Produit
from clients import services as clients
from clients.models import Client
from commandes import services as commandes
from commandes.models import Canal, Commande, LigneModeleRecurrent, ModeleRecurrent

PRODUITS = [
    ("BAG", "Pain baguette", 250),
    ("CRO", "Croissant", 300),
    ("MIE", "Pain de mie", 700),
    ("CHO", "Pain au chocolat", 350),  # prix fictif
]

# (nom, catégorie, téléphone, commune, adresse, conditions de paiement)
CLIENTS = [
    ("Restaurant Le Baobab", "restaurant", "07 00 00 00 42", "Cocody", "Deux-Plateaux, face pharmacie", "fin_de_mois"),  # noqa: E501
    ("Hôtel Ivoire Lagune", "entreprise", "07 00 00 00 43", "Cocody", "Boulevard Latrille", "fin_de_mois"),  # noqa: E501
    ("Supermarché Prosuma Riviera", "supermarche", "07 00 00 00 44", "Riviera", "Riviera 3, carrefour", "fin_de_mois"),  # noqa: E501
    ("Supérette Les Palmiers", "supermarche", "07 00 00 00 45", "Marcory", "Zone 4, rue 12", "comptant"),  # noqa: E501
    ("Traiteur Saveurs d'Akwaba", "evenement", "07 00 00 00 46", "Yopougon", "Siporex", "comptant"),
    ("Café Le Plateau", "restaurant", "07 00 00 00 47", "Plateau", "Avenue Chardy", "comptant"),
    ("Boutique Chez Fatou", "revendeur", "07 00 00 00 48", "Treichville", "Avenue 8", "comptant"),
]  # fmt: skip

# (client, jours (0 = lundi), créneau, {code produit: quantité}) — maquette 11
MODELES = [
    ("Restaurant Le Baobab", [0, 1, 2, 3, 4, 5], "06h–08h", {"BAG": 120, "CRO": 30, "MIE": 15}),
    ("Supérette Les Palmiers", [0, 1, 2, 3, 4, 5], "08h–10h", {"BAG": 100, "CRO": 60, "MIE": 20}),
    ("Café Le Plateau", [0, 1, 2, 3, 4], "06h–08h", {"BAG": 60, "CRO": 30}),
    ("Supermarché Prosuma Riviera", [0, 1, 2, 3, 4, 5, 6], "06h–08h", {"BAG": 300, "MIE": 80}),
    ("Hôtel Ivoire Lagune", [0, 1, 2, 3, 4, 5, 6], "06h–08h", {"BAG": 200, "CRO": 150, "CHO": 150}),
    ("Boutique Chez Fatou", [0, 1, 2, 3, 4, 5], "08h–10h", {"BAG": 40}),
]


def charger(sortie):
    produits = _produits()
    fiches = _clients()
    _modeles(fiches, produits)
    _commandes(fiches, produits)
    demain = timezone.localdate() + datetime.timedelta(days=1)
    rapport = commandes.generer_commandes_recurrentes(demain)
    # Plan de production du lendemain (Sprint 3) : récurrentes confirmées, sauf une,
    # pour montrer le bandeau « encore à confirmer ». Le plan reste à valider.
    for generee in rapport.creees[:-1]:
        commandes.confirmer_commande(generee, note="Confirmée (démonstration)")
    sortie.write(
        f"Données métier : {len(produits)} produits, {len(fiches)} clients, "
        f"{ModeleRecurrent.objects.count()} modèles récurrents, "
        f"{Commande.objects.count()} commandes "
        f"(dont {len(rapport.creees)} récurrente(s) générée(s) pour le {demain:%d/%m})."
    )


def _produits():
    resultat = {}
    for code, nom, prix in PRODUITS:
        produit = Produit.objects.filter(code=code).first()
        if produit is None:
            produit = catalogue.creer_produit(code=code, nom=nom, prix_reference=prix)
        resultat[code] = produit
    return resultat


def _clients():
    resultat = {}
    for nom, categorie, telephone, commune, adresse, conditions in CLIENTS:
        client = Client.objects.filter(nom=nom).first()
        if client is None:
            client = clients.creer_client(
                nom=nom,
                categorie=categorie,
                telephone=telephone,
                conditions_paiement=conditions,
                adresse={"libelle": "Adresse principale", "adresse": adresse, "commune": commune},
            )
        resultat[nom] = client
    return resultat


def _modeles(fiches, produits):
    for nom, jours, creneau, quantites in MODELES:
        client = fiches[nom]
        if ModeleRecurrent.objects.filter(client=client).exists():
            continue
        modele = ModeleRecurrent.objects.create(
            client=client,
            adresse=client.adresse_par_defaut,
            jours_semaine=jours,
            creneau=creneau,
            date_debut=timezone.localdate(),
        )
        for code, quantite in quantites.items():
            LigneModeleRecurrent.objects.create(
                modele=modele, produit=produits[code], quantite=quantite
            )


def _commandes(fiches, produits):
    """Quelques commandes ponctuelles à différents statuts (seulement la première fois)."""
    if Commande.objects.filter(modele_recurrent__isnull=True).exists():
        return
    demain = timezone.localdate() + datetime.timedelta(days=1)

    def nouvelle(client, lignes, **champs):
        return commandes.creer_commande(
            client=fiches[client],
            lignes=[(produits[code], quantite) for code, quantite in lignes.items()],
            canal=champs.pop("canal", Canal.WHATSAPP),
            date_livraison=demain,
            creneau=champs.pop("creneau", "06h–08h"),
            **champs,
        )

    nouvelle("Traiteur Saveurs d'Akwaba", {"BAG": 150, "CRO": 100}, creneau="10h–12h")  # brouillon
    commandes.soumettre_commande(
        nouvelle(
            "Café Le Plateau", {"CHO": 40}, canal=Canal.TELEPHONE, instructions="Livrer avant 7h"
        )
    )
    urgente = nouvelle(
        "Traiteur Saveurs d'Akwaba", {"BAG": 200, "CHO": 120}, creneau="10h–12h", urgente=True
    )
    commandes.soumettre_commande(urgente)  # demande de validation de l'urgence en attente
    confirmee = nouvelle("Restaurant Le Baobab", {"MIE": 10}, canal=Canal.COMMERCIAL)
    commandes.soumettre_commande(confirmee)
    commandes.confirmer_commande(confirmee, note="Confirmé par téléphone")
