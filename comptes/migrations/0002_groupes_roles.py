"""Crée un groupe Django par rôle de départ (§5)."""

from django.db import migrations

# Liste figée ici (une migration ne doit pas dépendre du code applicatif courant).
GROUPES = [
    "Direction",
    "Gestionnaire",
    "Commercial",
    "Production",
    "Préparation",
    "Chauffeur",
    "Facturation",
    "Comptabilité",
    "Prestataire",
]


def creer_groupes(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for nom in GROUPES:
        Group.objects.get_or_create(name=nom)


class Migration(migrations.Migration):
    dependencies = [
        ("comptes", "0001_initial"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    # Pas de retour arrière : aucune suppression de données.
    operations = [migrations.RunPython(creer_groupes, migrations.RunPython.noop)]
