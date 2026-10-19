# CLAUDE.md — ComandFlow

Ce fichier guide Claude Code (claude.ai/code) pour travailler dans ce dépôt.

## Projet

ComandFlow est une application web de gestion des commandes, de la production groupée, du chargement, des livraisons, de la facturation (FNE ou non) et des encaissements. Le client pilote est Mie Doré, une boulangerie en Côte d'Ivoire (50 à 150 commandes par jour). Le produit doit devenir un SaaS multi-entreprises.

Documents de référence, à lire avant toute tâche qui les concerne :

- `docs/cahier-des-charges.md` : besoins fonctionnels (sections numérotées §1 à §25).
- `docs/modele-donnees.md` : modèles, champs, machines à états, transitions et services. **Fait foi** pour la modélisation.
- `docs/stack-technique.md` : bibliothèques retenues et découpage en applications.
- `docs/maquettes/` : maquettes HTML des 17 écrans (ouvrir `index.html`). Elles fixent la disposition et les libellés attendus ; le code reprend ces libellés.
- `docs/plan-sprints.md` : découpage du MVP et état d'avancement.

## État du projet

Sprints 1 (socle) et 2 (clients, catalogue, commandes) terminés. Apps en place : `noyau`, `entreprises`, `comptes`, `catalogue`, `clients`, `commandes`. Écrans livrés : Clients (maquette 12), Commandes (maquettes 1, 9, 10), Récurrentes (maquette 11). Les autres écrans du menu sont des pages « en préparation » (`comptes/navigation.py`). Une commande s'arrête à « Confirmée » : le passage « À planifier » arrive avec la production (Sprint 3).

Les décisions métier prises avec l'utilisateur sont dans `docs/modele-donnees.md` §8.1 : les lire avant de toucher aux commandes, aux clients ou aux récurrentes.

Tout tourne dans Docker : le poste Windows n'a ni Python 3.12 ni Node. Les commandes passent par `docker compose exec web …` (voir « Commandes utiles »).

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

Précisions : réglages par variables d'environnement avec django-environ ; Tailwind via `django-tailwind-cli` (exécutable autonome, sans Node), source `assets/css/source.css` ; htmx et Alpine copiés dans `static/vendor/` (pas de CDN, exclus des hooks pre-commit). Stockage des fichiers privés : disque en développement, S3 managé (Cloudflare R2 ou Backblaze B2) en production ; pas de MinIO, qui n'est plus distribué.

## Règles d'architecture (non négociables)

- **Multi-entreprise** : chaque modèle métier hérite de `noyau.models.ModeleEntreprise` (FK `entreprise` + manager filtré). Aucune requête métier ne doit pouvoir lire les données d'une autre entreprise. Chaque vue et chaque service filtre par l'entreprise de l'utilisateur.
- **Traçabilité** : chaque modèle métier hérite de `noyau.models.ModeleTrace` (`cree_le`, `cree_par`, `modifie_le`, `modifie_par`). Les modèles sensibles (commande, lignes, chargement, livraison, facture, paiement, saisie de production, ainsi que `Validation` et `PieceJointe`) sont suivis par `django-pghistory`.
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

## Mécanismes du socle (à réutiliser, ne pas réinventer)

- **Entreprise et utilisateur courants** (`noyau/contexte.py`) : variables de contexte fixées par `entreprises.middleware.EntrepriseCouranteMiddleware` pour chaque requête. Hors requête (Celery, commande de gestion, test), les activer avec `activer_entreprise(...)` et `activer_utilisateur(...)`.
- **`ModeleEntreprise.objects`** filtre sur l'entreprise courante et **lève `AucuneEntrepriseCourante`** s'il n'y en a pas. Enregistrer un objet d'une autre entreprise lève `AutreEntreprise`. `toutes_entreprises` est sans filtre et réservé aux traitements système. `QuerySet.update()` et `bulk_create()` contournent `save()`, donc les auteurs et le contrôle d'entreprise : à éviter sur les modèles métier.
- **Services du noyau** (`noyau/services.py`) : `prochain_numero(prefixe, annuel=True)` ; `demander_validation()` et `decider_validation()` ; `joindre_fichier()`. Un fichier privé s'ouvre uniquement par `noyau.fichiers.url_signee(piece, utilisateur)` : lien personnel, limité à l'entreprise, qui expire après `DUREE_LIEN_FICHIER`.
- **Historique** : `@pghistory.track(model_name="Historique<Modele>")`. Les tables d'historique sont en ajout seul. Le contexte contient l'URL, l'utilisateur et l'entreprise ; l'auteur exact d'un événement est le champ `modifie_par` de l'instantané.
- **Rôles et écrans** : un rôle correspond à un groupe Django (`comptes.models.Role`). Les prédicats `a_le_role(...)`, `meme_entreprise` et `peut_gerer_utilisateurs` sont dans `comptes/rules.py`. Les écrans et la grille par rôle sont dans `comptes/navigation.py` (permission `ecrans.<code>`, URL nommée `ecran_<code>`), et la grille est documentée dans `docs/modele-donnees.md` §3.1. Quand un sprint livre un écran, il remplace l'URL « en préparation » de `comptes/urls.py` par celles de son app, **en gardant le nom `ecran_<code>`**, et la vue vérifie `ecrans.<code>`.
- **Code de vérification (2FA)** imposé à Direction et Comptabilité par `comptes.middleware.DoubleAuthentificationObligatoireMiddleware`. Le blocage après 5 échecs est géré par django-axes seul.
- **Gabarits** : `base_bureau.html` (barre de menus), `base_terrain.html` (mobile, sans menu) et `base_connexion.html` (pages d'allauth). Composants : `composants/champ.html`, `composants/messages.html` ; style des champs par `noyau.forms.FormulaireStyle` ; filtres `{% load format_fr %}` (`fcfa`, `quantite`, `jour_mois`, `date_courte`, `heure`).
- **Écrans livrés** : listés dans `ECRANS_LIVRES` (`comptes/urls.py`) ; leurs URL sont dans l'app (`config/urls.py`). Les listes rechargées par htmx renvoient un fragment `_<nom>.html` quand l'en-tête `HX-Request` est présent.
- **Administration Django** pour les référentiels sans écran (produits, modèles récurrents ; décision 8.1) : site `config.admin.SiteAdmin`, ouvert à la Direction et au Gestionnaire sans `is_staff`. Hériter de `noyau.admin.AdminEntreprise` (pas de suppression, entreprise implicite) et donner les permissions `<app>.view_…/add_…/change_…` et `<app>` dans `rules.py`. Un formulaire ne doit **jamais** lire un `ModeleEntreprise` au chargement du module (pas de `Meta.fields` avec clé étrangère dans un formulaire d'administration, `toutes_entreprises.none()` comme ensemble initial) : il n'y a pas d'entreprise courante, le serveur s'arrête.
- **Validation d'un responsable** : `Validation.contenu` porte le changement demandé ; l'app applique l'effet à l'acceptation (exemple : `commandes.services.decider_validation_commande`, qui vérifie aussi qui décide).
- **Commandes** : `commandes.services` (création, modification, transitions, demandes, récurrentes). `django-fsm` : `has_transition_perm()` vérifie aussi les conditions ; `Commande.full_clean()` exclut `statut`. Historique et frise de la fiche : `commandes/historique.py`, reconstruits depuis pghistory (aucune saisie en double).
- **Paramètres d'entreprise** : `entreprises/parametres.py` (créneaux, heures des récurrentes), lus dans `Entreprise.options` avec valeurs par défaut.
- **Tâches Celery** : `<app>/tasks.py`, planification dans `CELERY_BEAT_SCHEDULE` ; une tâche active l'entreprise qu'elle traite. Après modification d'une tâche, redémarrer `worker` et `beat`.
- **Import Excel/CSV** : `noyau.commandes_import.CommandeImport` (lecture, simulation, modèle de fichier, rapport) ; chaque ligne passe par les services de l'app.
- **Tests** : fabriques des `ModeleEntreprise` héritées de `noyau.tests.factories.FabriqueModeleEntreprise` ; pour créer un objet d'une autre entreprise pendant qu'un contexte est actif, l'entourer de `activer_entreprise(None)`.

## Enchaînements entre objets

La commande est le pivot. Elle peut avoir plusieurs chargements et plusieurs livraisons, ce qui permet de livrer un reliquat sans la dupliquer. Les cascades passent par les services listés en §7 de `docs/modele-donnees.md` : `production.services.integrer_commande / recalculer_besoin / valider_plan / enregistrer_saisie`, `logistique.services.affecter_commande / demarrer_tournee / cloturer_livraison`, `facturation.services.enregistrer_choix`, `paiements.services.affecter_paiement`. Un retour n'est pas un statut mais un objet `Retour`. Les preuves obligatoires sont lues dans `Entreprise.options` par une condition FSM.

## Structure

```
config/          settings/{base,dev,prod}.py, urls.py, celery.py
noyau/           classes abstraites, Compteur, Validation, PieceJointe, utilitaires
entreprises/ comptes/ clients/ catalogue/ commandes/ production/
logistique/ facturation/ paiements/ notifications/ pilotage/
templates/       gabarits HTMX ; static/ (vendor/) ; assets/css/ (source Tailwind) ; tests/ dans chaque app
docs/            documentation de conception
```

Les tests utilisent `config/settings/test.py`, qui installe l'app `noyau.tests` (modèle concret `ObjetTest` pour tester les classes abstraites). Les fixtures communes (`entreprise`, `utilisateur`) sont dans `conftest.py`, et `UtilisateurFactory(roles=[Role.X])` place l'utilisateur dans les groupes voulus.

## Tests

- Chaque transition FSM a au moins un test « autorisé » et un test « refusé » (mauvais statut, mauvais rôle, condition manquante).
- Chaque service a un test de cascade (les statuts liés évoluent ensemble) et un test d'annulation complète en cas d'erreur.
- Un test d'isolation multi-entreprise par app (un utilisateur de l'entreprise A ne voit rien de B).
- Les scénarios de recette du §22 du cahier des charges deviennent des tests d'intégration.

## Commandes utiles

```bash
docker compose up -d --build                         # PostgreSQL, Redis, Django (port PORT_WEB, 8001 sur ce poste)
docker compose exec web python manage.py makemigrations && docker compose exec web python manage.py migrate
docker compose exec web pytest                       # tous les tests
docker compose exec web pytest noyau/tests/test_compteur.py::test_numerotation_annuelle   # un seul test
docker compose exec web pytest commandes/ -k transition                                    # ciblé
docker compose exec web ruff check . && docker compose exec web ruff format .
docker compose exec web python manage.py tailwind build   # sous Windows, après ajout de classes Tailwind
docker compose exec web python manage.py charger_demo     # comptes et données de démonstration (mot de passe affiché)
docker compose restart worker beat                        # après modification d'une tâche Celery
docker compose exec web python manage.py importer_clients clients.xlsx --entreprise mie-dore --simulation
py -m pre_commit run --all-files                     # hooks (installés côté Windows)
```
