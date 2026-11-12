"""Réglages de l'entreprise depuis l'écran Paramètres (maquette 17, décision 8.6).

Les valeurs sont écrites dans `Entreprise.options`, lues par `entreprises.parametres` ;
l'historique des modifications est conservé (pghistory).
"""

import datetime

from django.core.exceptions import PermissionDenied
from django.db import transaction

from noyau.contexte import entreprise_obligatoire, utilisateur_courant

from .models import Entreprise
from .parametres import MOYENS_DE_PREUVE, bornes_creneau, exigences_de_preuve


class ActionImpossible(Exception):
    """Réglage refusé ; le message est affiché tel quel."""


def _entreprise_modifiable():
    utilisateur = utilisateur_courant()
    if utilisateur is None or not utilisateur.has_perm("entreprises.modifier_parametres"):
        raise PermissionDenied
    return Entreprise.objects.select_for_update().get(pk=entreprise_obligatoire().pk)


@transaction.atomic
def enregistrer_regles(*, generation, limite, resume, creneaux):
    """Heures des récurrentes et du résumé, créneaux de livraison (« 06h–08h »)."""
    entreprise = _entreprise_modifiable()
    if limite <= generation:
        raise ActionImpossible(
            "L'heure limite de confirmation doit suivre l'heure de génération des récurrentes."
        )
    propres = []
    for creneau in creneaux:
        creneau = creneau.strip()
        if not creneau:
            continue
        bornes = bornes_creneau(creneau)
        if bornes is None or bornes[1] <= bornes[0]:
            raise ActionImpossible(f"Créneau « {creneau} » : écrire par exemple 06h–08h.")
        if creneau in propres:
            raise ActionImpossible(f"Créneau « {creneau} » en double.")
        propres.append(creneau)
    if not propres:
        raise ActionImpossible("Indiquer au moins un créneau de livraison.")
    options = dict(entreprise.options or {})
    options.update(
        heure_generation_recurrentes=_texte(generation),
        heure_limite_confirmation=_texte(limite),
        heure_resume=_texte(resume),
        creneaux=propres,
    )
    entreprise.options = options
    entreprise.save(update_fields=["options"])
    return entreprise


def _texte(heure: datetime.time):
    return heure.strftime("%H:%M")


@transaction.atomic
def enregistrer_preuves(reglages):
    """`reglages` : {catégorie: (moyens cochés, « un » ou « tous »)} (décision 8.6)."""
    entreprise = _entreprise_modifiable()
    preuves = {}
    for categorie, (moyens, mode) in reglages.items():
        moyens = [moyen for moyen, _ in MOYENS_DE_PREUVE if moyen in moyens]
        if not moyens:
            raise ActionImpossible(f"{categorie.label} : cocher au moins un moyen de preuve.")
        if mode not in ("un", "tous"):
            raise ActionImpossible(f"{categorie.label} : choisir « un seul suffit » ou « tous ».")
        preuves[str(categorie)] = exigences_de_preuve(moyens, mode)
    options = dict(entreprise.options or {})
    options["preuves"] = preuves
    entreprise.options = options
    entreprise.save(update_fields=["options"])
    return entreprise
