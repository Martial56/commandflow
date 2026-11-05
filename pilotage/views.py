"""Tableau de bord (maquette 8, §16.2) et son export Excel ; contenu selon le rôle."""

import datetime

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.utils import timezone

from commandes.models import Commande
from noyau.exports import reponse_excel

from . import indicateurs


def _date(request):
    try:
        return datetime.date.fromisoformat(request.GET.get("date") or "")
    except ValueError:
        return timezone.localdate()


def _contenu(request, date):
    utilisateur = request.user
    siennes = not utilisateur.has_perm("pilotage.voir_toutes_commandes")
    contexte = {
        "date": date,
        "aujourd_hui": timezone.localdate(),
        "heure": timezone.localtime(),
        "seulement_les_siennes": siennes,
        "commandes": indicateurs.commandes(date, utilisateur, siennes),
        "alertes": indicateurs.alertes(utilisateur, siennes),
    }
    if utilisateur.has_perm("pilotage.voir_suivi"):
        contexte["tournees"] = indicateurs.tournees_en_cours()
    if utilisateur.has_perm("pilotage.voir_creances"):
        contexte["creances"] = indicateurs.creances()
    return contexte


@login_required
def tableau(request):
    if not request.user.has_perm("ecrans.tableau_de_bord"):
        raise PermissionDenied
    return render(request, "pilotage/tableau.html", _contenu(request, _date(request)))


@login_required
def export(request):
    """Synthèse de la date et liste des commandes à livrer (Excel, §16.2)."""
    if not request.user.has_perm("ecrans.tableau_de_bord"):
        raise PermissionDenied
    date = _date(request)
    contenu = _contenu(request, date)
    chiffres = contenu["commandes"]
    synthese = [
        ("Commandes du jour", chiffres["total"]),
        ("dont récurrentes", chiffres["recurrentes"]),
        ("Livrées", chiffres["livrees"]),
        ("dont partielles", chiffres["partielles"]),
        ("En anomalie (toutes dates)", chiffres["anomalies"]),
        *((barre["libelle"], barre["nombre"]) for barre in chiffres["barres"]),
    ]
    if "creances" in contenu:
        synthese += [
            ("Créances échues (FCFA)", contenu["creances"]["total"]),
            ("Clients en retard", contenu["creances"]["clients"]),
        ]
    commandes = Commande.objects.filter(date_livraison=date).exclude(
        statut__in=indicateurs.EXCLUES_DU_JOUR
    )
    if contenu["seulement_les_siennes"]:
        commandes = commandes.filter(cree_par=request.user)
    lignes = [
        (
            c.numero,
            c.client.nom,
            c.creneau,
            c.get_statut_display(),
            c.libelle_canal,
            c.montant_total,
        )
        for c in commandes.select_related("client").order_by("creneau", "numero")
    ]
    return reponse_excel(
        f"tableau-de-bord-{date:%Y%m%d}",
        [
            ("Synthèse", ["Indicateur", "Valeur"], synthese, set()),
            (
                "Commandes",
                ["Commande", "Client", "Créneau", "Statut", "Canal", "Montant"],
                lignes,
                {"Montant"},
            ),
        ],
    )
