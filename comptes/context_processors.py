from dataclasses import dataclass

from django.utils.module_loading import import_string

from .navigation import Ecran, menus


@dataclass(frozen=True)
class EntreeMenu:
    ecran: Ecran
    compteur: int = 0

    @property
    def libelle(self):
        return self.ecran.libelle

    @property
    def nom_url(self):
        return self.ecran.nom_url


def navigation(request):
    """Menus de la barre latérale, calculés côté serveur selon les droits.

    Un écran peut afficher une pastille d'alerte (`Ecran.compteur`).
    """
    utilisateur = getattr(request, "user", None)
    if utilisateur is None or not utilisateur.is_authenticated:
        return {}
    entrees = []
    for ecran in menus(utilisateur):
        nombre = import_string(ecran.compteur)(utilisateur) if ecran.compteur else 0
        entrees.append(EntreeMenu(ecran, nombre))
    return {"menus": entrees}
