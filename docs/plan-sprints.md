# ComandFlow — Plan des sprints du MVP

Chaque sprint se termine par une démonstration du parcours concerné et un tag git (`sprint-1`, `sprint-2`…).
Coche les cases au fur et à mesure.

## Étape 0 — Conception (terminée)

- [x] Analyse du cahier des charges et choix de la stack
- [x] Modèle de données et machines à états (`docs/modele-donnees.md`)
- [x] Maquettes complètes : 17 écrans (`docs/maquettes/index.html`)
- [ ] Arbitrages Mie Doré (section 8 de `docs/modele-donnees.md`)

## Sprint 1 — Socle

- [ ] Projet Django, `config/settings/{base,dev,prod}.py`, variables d'environnement (django-environ)
- [ ] Docker Compose : PostgreSQL 16 + Redis
- [ ] Outillage : ruff, pre-commit, pytest-django, factory_boy, GitHub Actions
- [ ] App `noyau` : `ModeleEntreprise`, `ModeleTrace`, `Compteur`, `Validation`, `PieceJointe`
- [ ] App `entreprises` : `Entreprise` + middleware/utilitaire « entreprise courante »
- [ ] App `comptes` : `Utilisateur` personnalisé, groupes/rôles, django-rules, allauth, axes
- [ ] django-pghistory configuré
- [ ] Gabarit de base HTMX + Tailwind + navigation par rôle
- [ ] Commande de données de démo (`python manage.py charger_demo`)

## Sprint 2 — Clients, catalogue, commandes

- [ ] Apps `clients` et `catalogue` + import Excel/CSV
- [ ] App `commandes` : modèles, FSM, services, écrans (maquette 1)
- [ ] Modèles récurrents + tâche Celery de génération quotidienne

## Sprint 3 — Production groupée

- [ ] Plan de production, besoins, saisies cumulatives, écarts, alertes (maquette 2)

## Sprint 4 — Chargement et tournées

- [ ] Tournées, chargements, double validation préparateur / chauffeur (maquette 3)
- [ ] Départ de tournée et cascade vers les commandes

## Sprint 5 — Application chauffeur

- [ ] Livraisons, preuves, échecs, reliquats, retours (maquette 4), PWA installable

## Sprint 6 — Facturation, paiements, pilotage

- [ ] Choix FNE / non-FNE + archivage, paiements, affectations, créances
- [ ] Tableau de bord essentiel, exports Excel/PDF

## Recette

- [ ] Scénarios §22 automatisés (Playwright) ; pilote d'une semaine en parallèle de l'existant

---

## Prompts pour Claude Code

À utiliser en **mode plan** (Shift+Tab) dans Claude Code. Remplace le numéro de sprint selon l'avancement.

### Démarrer un sprint

```
Lis CLAUDE.md, docs/plan-sprints.md et docs/modele-donnees.md.
Propose le plan détaillé du sprint 1 : découpage en étapes courtes, fichiers à créer,
fichiers à modifier, commandes, tests prévus pour chaque étape.
Ne code rien avant ma validation.
```

### Exécuter une étape validée

```
Plan validé. Exécute uniquement l'étape 1.
À la fin : lance les tests, montre le résultat, propose un message de commit.
Ne passe pas à l'étape suivante sans mon accord.
```

### Revue avant commit

```
Fais une revue de cette étape par rapport à CLAUDE.md :
multi-entreprise, traçabilité, PROTECT, FSM protégés, services transactionnels,
nommage en français, tests autorisé/refusé. Liste les écarts sans les corriger.
```

### Implémenter une machine à états

```
Implémente la machine à états de <Commande|Chargement|Livraison> exactement selon
la section correspondante de docs/modele-donnees.md : transitions, rôles, conditions.
Écris d'abord les tests (un autorisé et un refusé par transition), puis le code.
```
