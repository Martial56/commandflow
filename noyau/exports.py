"""Exports Excel (openpyxl) et PDF (WeasyPrint) des états de synthèse (§15, §16.2).

Les PDF sont produits à partir de gabarits dédiés (`templates/pdf/`), avec leur propre
feuille de style : ils ne dépendent ni de Tailwind ni des polices en ligne.
"""

import io

from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

TYPE_EXCEL = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
FORMAT_FCFA = "#,##0"


def _nom_fichier(nom, extension):
    return f"{nom}-{timezone.localdate():%Y-%m-%d}.{extension}"


def classeur(feuilles):
    """Classeur Excel : [(titre, entêtes, lignes, colonnes_en_fcfa)]."""
    document = Workbook()
    document.remove(document.active)
    for titre, entetes, lignes, colonnes_fcfa in feuilles:
        feuille = document.create_sheet(titre[:31])
        feuille.append(entetes)
        for cellule in feuille[1]:
            cellule.font = Font(bold=True)
        for ligne in lignes:
            feuille.append(list(ligne))
        for indice, entete in enumerate(entetes, start=1):
            colonne = get_column_letter(indice)
            largeur = max(
                [len(str(entete))] + [len(str(ligne[indice - 1] or "")) for ligne in lignes]
            )
            feuille.column_dimensions[colonne].width = min(max(largeur + 2, 10), 45)
            if entete in colonnes_fcfa:
                for (cellule,) in feuille.iter_rows(min_row=2, min_col=indice, max_col=indice):
                    cellule.number_format = FORMAT_FCFA
        feuille.freeze_panes = "A2"
    tampon = io.BytesIO()
    document.save(tampon)
    return tampon.getvalue()


def reponse_excel(nom, feuilles):
    reponse = HttpResponse(classeur(feuilles), content_type=TYPE_EXCEL)
    reponse["Content-Disposition"] = f'attachment; filename="{_nom_fichier(nom, "xlsx")}"'
    return reponse


def pdf(gabarit, contexte, request=None):
    """PDF d'un gabarit de `templates/pdf/` (import local : WeasyPrint est lourd)."""
    from weasyprint import HTML

    html = render_to_string(gabarit, contexte, request=request)
    return HTML(string=html).write_pdf()


def reponse_pdf(nom, gabarit, contexte, request=None):
    reponse = HttpResponse(pdf(gabarit, contexte, request), content_type="application/pdf")
    reponse["Content-Disposition"] = f'inline; filename="{_nom_fichier(nom, "pdf")}"'
    return reponse
