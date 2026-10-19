"""Import des clients : python manage.py importer_clients clients.xlsx --entreprise mie-dore

- Sans `code` : le client est créé (code CLI-… attribué), avec son adresse par défaut et
  son contact s'ils sont renseignés. Un client de même nom et même téléphone déjà
  présent est ignoré : une relance du même fichier ne crée pas de doublon.
- Avec `code` : la fiche existante est mise à jour avec les cellules renseignées
  (les cellules vides ne l'effacent pas). Adresses et contacts ne sont pas modifiés.
`--modele clients.xlsx` écrit un fichier modèle à remplir.
"""

from clients import services
from clients.models import Categorie, Client, ConditionsPaiement
from noyau.commandes_import import CommandeImport
from noyau.importation import LigneRejetee, choix, nombre

LIBELLE_ADRESSE_PAR_DEFAUT = "Adresse principale"


class Command(CommandeImport):
    help = "Importe ou met à jour les clients depuis un fichier .xlsx ou .csv."

    colonnes = [
        "code",
        "nom",
        "categorie",
        "telephone",
        "ncc",
        "conditions_paiement",
        "delai_paiement_jours",
        "notes",
        "adresse_libelle",
        "adresse",
        "commune",
        "repere",
        "latitude",
        "longitude",
        "consignes",
        "contact_nom",
        "contact_fonction",
        "contact_telephone",
    ]
    obligatoires = ["nom", "categorie", "telephone"]
    exemple = {
        "nom": "Restaurant Le Baobab",
        "categorie": "Restaurant",
        "telephone": "07 00 00 00 01",
        "conditions_paiement": "Fin de mois",
        "adresse_libelle": "Cuisine principale",
        "adresse": "Deux-Plateaux, face pharmacie",
        "commune": "Cocody",
        "consignes": "Entrée de service",
        "contact_nom": "M. Koffi",
        "contact_fonction": "gérant",
    }

    def traiter_ligne(self, donnees):
        champs = self._champs_client(donnees)
        code = donnees.get("code", "")
        if code:
            return self._mettre_a_jour(code, champs)

        try:
            services.creer_client(
                adresse=self._adresse(donnees), contact=self._contact(donnees), **champs
            )
        except services.DoublonPotentiel:
            return "ignore"
        return "cree"

    def _champs_client(self, donnees):
        champs = {
            "nom": donnees.get("nom", ""),
            "categorie": choix(donnees.get("categorie"), Categorie, "Catégorie"),
            "telephone": donnees.get("telephone", ""),
            "ncc": donnees.get("ncc", ""),
            "notes": donnees.get("notes", ""),
        }
        delai = nombre(donnees.get("delai_paiement_jours"), "Délai de paiement", entier=True)
        if donnees.get("conditions_paiement"):
            champs["conditions_paiement"] = choix(
                donnees["conditions_paiement"], ConditionsPaiement, "Conditions de paiement"
            )
        elif delai is not None:
            champs["conditions_paiement"] = ConditionsPaiement.X_JOURS
        if delai is not None:
            champs["delai_paiement_jours"] = int(delai)
        return champs

    def _mettre_a_jour(self, code, champs):
        client = Client.objects.filter(code=code).first()
        if client is None:
            raise LigneRejetee(f"code client « {code} » inconnu dans cette entreprise.")
        modifies = {
            c: v for c, v in champs.items() if v not in ("", None) and getattr(client, c) != v
        }
        if not modifies:
            return "ignore"
        services.modifier_client(client, **modifies)
        return "mis_a_jour"

    @staticmethod
    def _adresse(donnees):
        champs = ["adresse", "commune", "repere", "consignes", "latitude", "longitude"]
        if not any(donnees.get(c) for c in champs):
            return None
        return {
            "libelle": donnees.get("adresse_libelle") or LIBELLE_ADRESSE_PAR_DEFAUT,
            "adresse": donnees.get("adresse", ""),
            "commune": donnees.get("commune", ""),
            "repere": donnees.get("repere", ""),
            "consignes": donnees.get("consignes", ""),
            "latitude": nombre(donnees.get("latitude"), "Latitude"),
            "longitude": nombre(donnees.get("longitude"), "Longitude"),
        }

    @staticmethod
    def _contact(donnees):
        if not donnees.get("contact_nom"):
            return None
        return {
            "nom": donnees["contact_nom"],
            "fonction": donnees.get("contact_fonction", ""),
            "telephone": donnees.get("contact_telephone", ""),
        }
