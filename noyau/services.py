from django.db import transaction
from django.utils import timezone

from .contexte import AucuneEntrepriseCourante, entreprise_courante
from .models import Compteur


@transaction.atomic
def prochain_numero(prefixe, *, annuel=True, entreprise=None, date_reference=None):
    """Attribue le prochain numéro métier : `CMD-2026-000123`, ou `CLI-000123` si non annuel.

    Le compteur est verrouillé (`select_for_update`) jusqu'à la fin de la transaction :
    deux appels simultanés ne peuvent pas obtenir le même numéro, et un numéro n'est pas
    consommé si la transaction appelante est annulée.
    """
    entreprise = entreprise or entreprise_courante()
    if entreprise is None:
        raise AucuneEntrepriseCourante(f"Numérotation {prefixe} sans entreprise courante.")
    annee = (date_reference or timezone.localdate()).year if annuel else None

    compteur, _ = Compteur.objects.get_or_create(
        entreprise=entreprise, prefixe=prefixe, annee=annee
    )
    compteur = Compteur.objects.select_for_update().get(pk=compteur.pk)
    compteur.dernier_numero += 1
    compteur.save(update_fields=["dernier_numero"])

    if annuel:
        return f"{prefixe}-{annee}-{compteur.dernier_numero:06d}"
    return f"{prefixe}-{compteur.dernier_numero:06d}"
