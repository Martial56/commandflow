# CLAUDE.md — ComandFlow

## Projet

ComandFlow est une application web de gestion des commandes, de la production groupée, du chargement, des livraisons, de la facturation (FNE ou non) et des encaissements. Le client pilote est Mie Doré, une boulangerie en Côte d'Ivoire (50 à 150 commandes par jour). Le produit doit devenir un SaaS multi-entreprises.

Documents de référence, à lire avant toute tâche qui les concerne :

- `docs/cahier-des-charges.md` : besoins fonctionnels (sections numérotées §1 à §25).
- `docs/modele-donnees.md` : modèles, champs, machines à états, transitions et services. **Fait foi** pour la modélisation.
- `docs/stack-technique.md` : bibliothèques retenues et découpage en applications.
- `docs/maquettes/` : maquettes HTML des 17 écrans (ouvrir `index.html`). Elles fixent la disposition et les libellés attendus ; le code reprend ces libellés.
- `docs/plan-sprints.md` : découpage du MVP et état d'avancement.

## Règles de travail (obligatoires)

1. **Plan d'abord.** Pour toute tâche non triviale, propose un plan numéroté (fichiers à créer, fichiers à modifier, commandes à lancer) et **attends ma validation** avant d'écrire du code.
2. **Incréments courts.** Une étape = un objectif vérifiable. Après chaque étape : tests verts, puis propose un message de commit. Ne commite pas sans mon accord.
3. **Précision des modifications.** Quand tu modifies un fichier existant, indique clairement ce qui est **remplacé** et ce qui est **ajouté**.
4. **Pas d'invention métier.** Si une règle n'est pas dans `docs/`, pose la question au lieu de supposer. Les points ouverts sont listés en section 8 de `docs/modele-donnees.md`.
5. **Pas d'action destructive** (suppression de fichiers, `flush`, `DROP`, réécriture d'historique git, migrations écrasées) sans demande explicite.
6. Mets à jour `docs/plan-sprints.md` (cases cochées) à la fin de chaque étape validée.

## Langue et conventions de nommage

- **Tout en français** : noms de modèles, champs, fonctions, variables métier, commentaires, docstrings, libellés, messages, `verbose_name`. Exemples : `Commande`, `LigneCommande`, `date_livraison`, `confirmer()`, `cree_par`.
- Sans accents dans les identifiants Python (`quantite_livree`, pas `quantité_livrée`) ; avec accents dans les libellés affichés.
- Termes techniques anglais conservés quand ils sont standards (`views.py`, `services.py`, `queryset`, `settings`).
- Numéros métier : `CMD-AAAA-000123`, `TRN-…`, `FAC-…`, `CLI-000123`.

## Stack

Python 3.12, Django 5.2 LTS, PostgreSQL 16, Redis, Celery + Celery Beat, HTMX + Alpine.js + Tailwind CSS, django-fsm-2, django-pghistory, django-rules, django-allauth, django-axes, django-storages (S3), django-import-export, WeasyPrint. Tests : pytest-django + factory_boy. Qualité : ruff + pre-commit. Environnement : Docker Compose.

## Règles d'architecture (non négociables)

- **Multi-entreprise** : chaque modèle métier hérite de `noyau.models.ModeleEntreprise` (FK `entreprise` + manager filtré). Aucune requête métier ne doit pouvoir lire les données d'une autre entreprise. Chaque vue et chaque service filtre par l'entreprise de l'utilisateur.
- **Traçabilité** : chaque modèle métier hérite de `noyau.models.ModeleTrace` (`cree_le`, `cree_par`, `modifie_le`, `modifie_par`). Les modèles sensibles (commande, lignes, chargement, livraison, facture, paiement, saisie de production) sont suivis par `django-pghistory`.
- **Aucune suppression physique** : `on_delete=models.PROTECT` partout ; annulation par statut ; référentiels désactivés via `actif=False`.
- **Statuts** : `FSMField(protected=True)`. Un statut ne change **que** par une transition décorée `@transition`, avec ses conditions et permissions, conformément aux tables de `docs/modele-donnees.md`.
- **Couche services** : la logique métier et les effets en cascade vivent dans `<app>/services.py`, dans `transaction.atomic()`. Une vue appelle un service ; le service appelle les transitions. **Pas de signaux Django** pour la logique métier. Une vue ne modifie jamais un statut directement.
- **Montants** : `DecimalField(max_digits=14, decimal_places=0)` en FCFA. Jamais de `FloatField` pour l'argent.
- **Quantités** : `DecimalField(max_digits=10, decimal_places=3)`.
- **Prix figé** : le prix unitaire est copié sur chaque ligne de commande à la création.
- **Numérotation** : via `noyau.models.Compteur` avec `select_for_update()`.
- **Décisions** : toute validation par un responsable passe par le modèle `noyau.models.Validation`.
- **Permissions** : contrôlées côté serveur (django-rules), jamais seulement dans les templates.
- **Fichiers** (bordereaux, photos, signatures) : stockage privé, accès par URL signée.
- **Fuseau** : `USE_TZ = True`, `TIME_ZONE = "Africa/Abidjan"`, `LANGUAGE_CODE = "fr"`.

## Structure

```
config/          settings/{base,dev,prod}.py, urls.py, celery.py
noyau/           classes abstraites, Compteur, Validation, PieceJointe, utilitaires
entreprises/ comptes/ clients/ catalogue/ commandes/ production/
logistique/ facturation/ paiements/ notifications/ pilotage/
templates/       gabarits HTMX ; static/ ; tests/ dans chaque app
docs/            documentation de conception
```

## Tests

- Chaque transition FSM a au moins un test « autorisé » et un test « refusé » (mauvais statut, mauvais rôle, condition manquante).
- Chaque service a un test de cascade (les statuts liés évoluent ensemble) et un test d'annulation complète en cas d'erreur.
- Un test d'isolation multi-entreprise par app (un utilisateur de l'entreprise A ne voit rien de B).
- Les scénarios de recette du §22 du cahier des charges deviennent des tests d'intégration.

## Commandes utiles

```bash
docker compose up -d                       # PostgreSQL + Redis
python manage.py runserver
python manage.py makemigrations && python manage.py migrate
pytest                                     # tous les tests
pytest commandes/ -k transition            # ciblé
ruff check . && ruff format .
```
