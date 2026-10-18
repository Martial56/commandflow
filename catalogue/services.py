from django.db import transaction

from noyau.contexte import entreprise_obligatoire

from .models import Produit

CHAMPS_MODIFIABLES = {"code", "nom", "unite", "prix_reference", "notes_preparation"}


@transaction.atomic
def creer_produit(**donnees):
    """Crée un produit dans l'entreprise courante, après validation complète."""
    produit = Produit(entreprise=entreprise_obligatoire(), **donnees)
    produit.full_clean()
    produit.save()
    return produit


@transaction.atomic
def modifier_produit(produit, **donnees):
    """Modifie un produit. Le prix des commandes existantes n'est pas touché (prix figé)."""
    inconnus = set(donnees) - CHAMPS_MODIFIABLES
    if inconnus:
        raise ValueError(f"Champs non modifiables : {', '.join(sorted(inconnus))}")
    produit = Produit.objects.select_for_update().get(pk=produit.pk)
    for champ, valeur in donnees.items():
        setattr(produit, champ, valeur)
    produit.full_clean()
    produit.save()
    return produit


@transaction.atomic
def desactiver_produit(produit):
    """Retire le produit des nouvelles commandes ; il reste visible dans l'historique."""
    return _changer_activite(produit, actif=False)


@transaction.atomic
def reactiver_produit(produit):
    return _changer_activite(produit, actif=True)


def _changer_activite(produit, *, actif):
    produit = Produit.objects.select_for_update().get(pk=produit.pk)
    produit.actif = actif
    produit.save(update_fields=["actif"])
    return produit
