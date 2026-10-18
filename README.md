# ComandFlow

Gestion des commandes, de la production, des livraisons, de la facturation et des encaissements.
Projet pilote : Mie Doré.

## Documentation

| Fichier | Contenu |
| --- | --- |
| `CLAUDE.md` | Règles de travail et d'architecture (lu automatiquement par Claude Code) |
| `docs/cahier-des-charges.md` | Besoins fonctionnels |
| `docs/modele-donnees.md` | Modèle de données, machines à états, services |
| `docs/stack-technique.md` | Outils retenus et découpage en applications |
| `docs/plan-sprints.md` | Plan du MVP, avancement et prompts Claude Code |
| `docs/maquettes/` | Maquettes HTML des 17 écrans (ouvrir `index.html` dans un navigateur) |

## Commandes Claude Code du projet

- `/sprint 1` : propose le plan détaillé d'un sprint, sans coder.
- `/revue` : vérifie les changements en cours par rapport à `CLAUDE.md`.

## Démarrage

Prérequis : Docker Desktop. Python et Node ne sont pas nécessaires sur le poste : Django tourne dans le conteneur `web` (Python 3.12).

```bash
cp .env.example .env            # puis remplacer DJANGO_SECRET_KEY
docker compose up -d --build    # PostgreSQL 16, Redis, Django sur http://localhost:8000 (port réglable via PORT_WEB)
docker compose exec web pytest  # tests
docker compose exec web python manage.py <commande>
docker compose exec web python manage.py charger_demo   # comptes de démonstration (développement)
```

Le CSS (Tailwind, sans Node) est compilé au démarrage du conteneur `web`, puis à chaque modification sous Linux et macOS. **Sous Windows**, Docker ne transmet pas les modifications de fichiers au conteneur : après avoir ajouté des classes Tailwind dans un gabarit, relancer la compilation :

```bash
docker compose exec web python manage.py tailwind build
```
