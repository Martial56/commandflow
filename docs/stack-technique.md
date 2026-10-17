# ComandFlow — Stack technique retenue

## Application

| Besoin | Outil | Raison |
| --- | --- | --- |
| Backend | Django 5.2 LTS + PostgreSQL 16 | Stack maîtrisée, support long terme, admin Django en back-office de secours |
| Interface bureau | HTMX + Alpine.js + Tailwind CSS | Réactif sans SPA séparée |
| Interface terrain | PWA : service worker (Workbox) + IndexedDB (Dexie.js) | Installable sur Android ; hors connexion en phase 2 |
| Statuts | django-fsm-2 (ou viewflow.fsm) | Transitions déclarées avec conditions et permissions |
| Audit | django-pghistory | Historique par triggers PostgreSQL |
| Permissions | django-rules + groupes Django | Règles objet, contrôle côté serveur |
| Multi-entreprises | FK `entreprise` + manager filtré (RLS PostgreSQL plus tard) | Prêt pour le SaaS |
| Authentification | django-allauth + 2FA (direction, comptabilité) + django-axes | Comptes individuels, anti-force brute |
| Tâches planifiées | Celery + Redis + Celery Beat (ou django-q2) | Récurrentes, alertes, résumé quotidien |
| Notifications | Polling HTMX (SSE plus tard) | Suffisant pour 50 à 150 commandes par jour |
| Fichiers privés | django-storages + S3 (MinIO, Cloudflare R2 ou Backblaze B2) | URL signées à durée limitée |
| Photo | `<input capture="environment">` + browser-image-compression | Photos légères en 3G/4G |
| Signature | signature_pad | Signature à l'écran |
| Cartes | Leaflet + OpenStreetMap ; lien vers l'appli de navigation | Gratuit |
| Optimisation tournées (phase 2-3) | VROOM + OSRM ou OpenRouteService | Open source |
| Import / export | django-import-export, WeasyPrint | Excel/CSV, PDF |
| Graphiques | Apache ECharts ou Chart.js | Tableaux de bord |
| FNE | MVP : archivage PDF + métadonnées ; API DGI en phase 3 | Conforme au cahier des charges |

## Qualité et exploitation

| Étape | Outils |
| --- | --- |
| Qualité | ruff, pre-commit, pytest-django, factory_boy |
| Recette | Playwright (scénarios §22) |
| CI | GitHub Actions |
| Déploiement | Docker Compose sur VPS + Caddy + Gunicorn ; staging et production séparés |
| Sauvegardes | pg_dump quotidien + restic hors site ; test de restauration mensuel |
| Supervision | Sentry + Uptime Kuma |

## Applications Django

```
comandflow/
├── config/          # settings (base, dev, prod), urls, celery
├── entreprises/     # tenant, paramètres, options activées
├── comptes/         # utilisateurs, rôles, permissions
├── clients/         # fiches, adresses, détection de doublons
├── catalogue/       # produits, prix
├── commandes/       # commandes, lignes, modèles récurrents, validations
├── production/      # plan groupé, saisies cumulatives, écarts
├── logistique/      # chargement, tournées, livraisons, preuves, retours
├── facturation/     # choix FNE / non-FNE, documents archivés
├── paiements/       # règlements, affectations, créances
├── notifications/   # alertes, résumé quotidien
├── pilotage/        # tableaux de bord, exports
└── noyau/           # classes abstraites, compteur, validation, utilitaires communs
```
