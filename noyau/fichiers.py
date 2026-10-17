"""Accès aux fichiers privés par lien signé, personnel et de durée limitée."""

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.http import FileResponse, Http404
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .contexte import entreprise_courante
from .models import PieceJointe

SEL = "noyau.fichiers"


def url_signee(piece_jointe, utilisateur):
    """Lien vers la pièce jointe, valable `DUREE_LIEN_FICHIER` secondes pour cet utilisateur."""
    jeton = signing.dumps({"pj": piece_jointe.pk, "u": utilisateur.pk}, salt=SEL)
    return reverse("noyau:fichier", args=[jeton])


@require_GET
@never_cache
@login_required
def telecharger_fichier(request, jeton):
    """Sert le fichier si le lien est valide, non expiré, émis pour cet utilisateur, et si la
    pièce appartient à son entreprise. Toute erreur répond 404, sans indiquer la cause."""
    try:
        donnees = signing.loads(jeton, salt=SEL, max_age=settings.DUREE_LIEN_FICHIER)
    except signing.BadSignature as erreur:  # inclut SignatureExpired
        raise Http404 from erreur
    if donnees.get("u") != request.user.pk or entreprise_courante() is None:
        raise Http404
    try:
        piece_jointe = PieceJointe.objects.get(pk=donnees.get("pj"))
    except PieceJointe.DoesNotExist as erreur:
        raise Http404 from erreur
    return FileResponse(piece_jointe.fichier.open("rb"), filename=piece_jointe.nom_original)
