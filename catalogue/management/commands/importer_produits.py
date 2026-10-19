"""Import des produits : python manage.py importer_produits produits.xlsx --entreprise mie-dore

La colonne `code` identifie le produit : un code existant est mis à jour, un code
nouveau crée le produit. Une relance du même fichier ne crée donc pas de doublon.
`--modele produits.xlsx` écrit un fichier modèle à remplir.
"""

from catalogue import services
from catalogue.models import Produit, Unite
from noyau.commandes_import import CommandeImport
from noyau.importation import LigneRejetee, choix, nombre, oui_non


class Command(CommandeImport):
    help = "Importe ou met à jour les produits du catalogue depuis un fichier .xlsx ou .csv."

    colonnes = ["code", "nom", "unite", "prix", "notes_preparation", "actif"]
    obligatoires = ["code", "nom", "unite", "prix"]
    exemple = {
        "code": "BAG",
        "nom": "Pain baguette",
        "unite": "pièce",
        "prix": 250,
        "notes_preparation": "",
        "actif": "oui",
    }

    def traiter_ligne(self, donnees):
        code = donnees.get("code", "")
        if not code:
            raise LigneRejetee("code manquant.")
        champs = {
            "code": code,
            "nom": donnees.get("nom", ""),
            "unite": choix(donnees.get("unite"), Unite, "Unité"),
            "prix_reference": nombre(donnees.get("prix"), "Prix", entier=True, obligatoire=True),
            "notes_preparation": donnees.get("notes_preparation", ""),
        }
        actif = oui_non(donnees.get("actif"))

        produit = Produit.objects.filter(code=code).first()
        if produit is None:
            produit = services.creer_produit(**champs)
            if not actif:
                services.desactiver_produit(produit)
            return "cree"

        modifies = {c: v for c, v in champs.items() if getattr(produit, c) != v}
        if not modifies and produit.actif == actif:
            return "ignore"
        if modifies:
            produit = services.modifier_produit(produit, **modifies)
        if produit.actif != actif:
            (services.reactiver_produit if actif else services.desactiver_produit)(produit)
        return "mis_a_jour"
