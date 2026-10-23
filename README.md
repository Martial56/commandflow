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

### Import des clients et des produits (Excel ou CSV)

```bash
# 1. Obtenir un fichier modèle à remplir (en-têtes + une ligne d'exemple)
docker compose exec web python manage.py importer_produits modele-produits.xlsx --modele
docker compose exec web python manage.py importer_clients modele-clients.xlsx --modele
# 2. Vérifier le fichier rempli sans rien enregistrer, puis importer
docker compose exec web python manage.py importer_produits produits.xlsx --entreprise mie-dore --utilisateur aminata.k --simulation
docker compose exec web python manage.py importer_produits produits.xlsx --entreprise mie-dore --utilisateur aminata.k
```

- **Produits** : colonnes obligatoires `code`, `nom`, `unite` (pièce, kg, carton), `prix` (FCFA entier). Un code existant est mis à jour.
- **Clients** : colonnes obligatoires `nom`, `categorie`, `telephone`. Sans `code`, le client est créé avec son adresse et son contact ; un client de même nom et même téléphone est ignoré. Avec `code`, la fiche est mise à jour (les cellules vides n'effacent rien).
- Une ligne en erreur est rejetée avec son motif, sans bloquer les autres ; relancer le même fichier ne crée pas de doublon. Les fichiers sont lus dans le dossier du projet (monté dans le conteneur).

Le CSS (Tailwind, sans Node) est compilé au démarrage du conteneur `web`, puis à chaque modification sous Linux et macOS. **Sous Windows**, Docker ne transmet pas les modifications de fichiers au conteneur : après avoir ajouté des classes Tailwind dans un gabarit, relancer la compilation :

```bash
docker compose exec web python manage.py tailwind build
```

## Hooks pre-commit

Les hooks (ruff, fins de ligne…) tournent dans le conteneur `web` : sous Windows, le contrôle
des applications bloque les outils que pre-commit installe sur le poste. À faire une fois :

```bash
cp outils/hook-pre-commit.sh .git/hooks/pre-commit
```

Docker doit être démarré pour committer. Pour lancer les hooks à la main :
`docker compose exec web pre-commit run --all-files`.
