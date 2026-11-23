# ComandFlow — Plan du pilote chez Mie Doré

**Premier jet, à compléter avec Mie Doré.** Les éléments marqués **« à valider »** sont des propositions : aucun n'est décidé.

## 1. Objet

Faire tourner ComandFlow pendant **une semaine, en parallèle de la méthode actuelle** (`docs/plan-sprints.md`). La méthode actuelle reste la référence pendant tout le pilote : en cas de doute ou de panne, c'est elle qui fait foi. Le pilote vérifie, en conditions réelles, que le parcours complet tient : commande, production, chargement, tournée, preuve de livraison, document FNE ou non, paiement.

## 2. Préalables (liste de contrôle)

| N° | Préalable | Responsable | État |
| --- | --- | --- | --- |
| P1 | Environnements de préparation et de production en ligne, en HTTPS, sur le nom de domaine retenu | Développement | ☐ |
| P2 | Sauvegardes quotidiennes en place **et une restauration essayée** sur la préparation | Développement | ☐ |
| P3 | Procès-verbal de recette signé (`docs/proces-verbal-recette.md`), sans réserve bloquante | Mie Doré, développement | ☐ |
| P4 | Réponses de Mie Doré : taux de TVA par produit, unités de vente, indicatif +225 automatique, historique des prix, heure du résumé quotidien | Mie Doré | ☐ |
| P5 | Catalogue réel importé : produits, unités, prix TTC, taux de TVA | Mie Doré (fichier), développement (import) | ☐ |
| P6 | Clients réels importés : catégorie, téléphone, adresses, conditions de paiement, NCC (commande `importer_clients`, en simulation d'abord) | Mie Doré (fichier), développement (import) | ☐ |
| P7 | Modèles de commandes récurrentes saisis (administration) | Gestionnaire | ☐ |
| P8 | Véhicules et prestataires saisis (administration) | Gestionnaire | ☐ |
| P9 | Comptes réels créés depuis l'écran Paramètres ; codes de vérification activés pour la Direction et la Comptabilité | Direction, Gestionnaire | ☐ |
| P10 | Réglages faits dans Paramètres : créneaux, heures des récurrentes et du résumé, preuves exigées par catégorie de client | Direction, Gestionnaire | ☐ |
| P11 | Téléphones des chauffeurs et de la préparation : Chrome récent, application installée depuis l'adresse HTTPS, appareil photo et position autorisés, forfait de données | Gestionnaire | ☐ |
| P12 | Formation de chaque profil (une séance courte par rôle, avec un guide imprimé) | Développement | ☐ |
| P13 | Données de démonstration absentes de la production (aucune commande `charger_demo`) | Développement | ☐ |
| P14 | Canal de signalement des anomalies choisi (groupe de discussion, téléphone…) — **à valider** | Mie Doré | ☐ |

## 3. Périmètre — à valider

- **Clients concernés.** *Proposition : un échantillon représentatif plutôt que tous les clients* — par exemple les clients d'une ou deux tournées, dont des clients récurrents, au moins un client facturé en FNE et au moins un client payé à terme.
- **Personnes concernées.** *Proposition : toutes les personnes qui interviennent sur ces clients*, chacune avec son propre compte.
- **Dates.** Du ……/……/2026 (J1) au ……/……/2026 (J7).

## 4. Déroulé

| Moment | Activités |
| --- | --- |
| J-7 à J-3 | Préalables P1 à P14 ; formation par rôle |
| J-2 | Répétition générale sur la préparation : une journée type jouée de bout en bout avec les vraies données |
| J-1 | Bascule des comptes et des données sur la production ; vérification de chaque connexion |
| J1 à J7 | Double saisie : chaque événement du périmètre est enregistré dans la méthode actuelle **et** dans ComandFlow |
| Chaque jour | Point de 15 minutes : rapprochement de la veille (section 5), anomalies (section 7) |
| J+1 | Bilan et décision (section 8) |

**Journée type dans ComandFlow** (heures par défaut, réglables dans Paramètres) :

| Heure | Qui | Quoi |
| --- | --- | --- |
| Veille 18 h | Système | Génération des commandes récurrentes du lendemain |
| Veille avant 20 h | Commercial, Gestionnaire | Confirmation des récurrentes ; saisie et confirmation des autres commandes |
| Matin | Production | Plan validé, fournées saisies au fur et à mesure, commandes marquées prêtes |
| Avant le départ | Préparation, puis chauffeur | Contrôle du chargement, réception par le chauffeur, démarrage de la tournée |
| Tournée | Chauffeur | Arrivée, preuve de livraison, partielles, échecs, retours |
| Après les livraisons | Facturation | Choix FNE ou autre document, PDF de la FNE joint |
| Dans la journée | Comptabilité | Paiements enregistrés et affectés |
| 7 h | Système | Résumé du jour pour la Direction et le Gestionnaire |

## 5. Rapprochement quotidien

Chaque matin, pour la veille et pour le périmètre du pilote :

| Contrôle | Méthode actuelle | ComandFlow | Écart | Explication |
| --- | --- | --- | --- | --- |
| Nombre de commandes et montant total | | | | |
| Quantités produites par produit | | | | |
| Livraisons : livrées, partielles, en échec | | | | |
| Retours et reliquats | | | | |
| Documents émis (FNE et autres) et montants | | | | |
| Encaissements et soldes des clients | | | | |

Les chiffres de ComandFlow se lisent sur le tableau de bord et dans les exports Excel (créances, paiements, documents). Tout écart doit être expliqué : erreur de saisie, oubli dans l'un des deux systèmes, ou anomalie de ComandFlow (section 7).

## 6. Critères de réussite — à valider

*Propositions :*

1. Toutes les commandes du périmètre sont dans ComandFlow, chaque jour.
2. Chaque écart du rapprochement est expliqué le lendemain ; aucun n'est dû à une perte de données de ComandFlow.
3. Chaque livraison du périmètre a sa preuve exigée, sans contournement.
4. Aucune anomalie bloquante n'est ouverte à la fin du pilote.
5. Les équipes jugent le temps de saisie acceptable (avis recueilli par rôle au bilan).

## 7. Anomalies

- **Signalement** : par le canal choisi (P14), avec l'écran, l'heure, le compte utilisé et si possible une capture.
- **Gravité** : bloquante (le travail ne peut pas continuer dans ComandFlow), majeure (contournement possible), mineure (confort, libellé).
- **Délai de correction** — *à valider* : bloquante dans la journée ; majeure avant la fin du pilote ; mineure après le bilan.
- Chaque anomalie est notée dans un registre (date, description, gravité, correction, date de mise en production).

## 8. Retour arrière

La méthode actuelle restant la référence, l'arrêt du pilote ne fait perdre aucune donnée de l'activité. En cas d'anomalie bloquante non corrigée dans la journée, la Direction peut **suspendre le pilote** : les équipes continuent avec la méthode actuelle seule. Les données saisies dans ComandFlow restent consultables et exportables (Excel), et le pilote reprend après correction.

## 9. Bilan et décision

À J+1 : critères de réussite (section 6), registre des anomalies, avis de chaque rôle. Trois issues possibles :

- **généralisation** à tous les clients, avec une date d'arrêt de la double saisie ;
- **prolongation** du pilote après corrections ;
- **arrêt**, avec la liste de ce qui manque.

## 10. Questions ouvertes

- Réponses de Mie Doré attendues (P4) et §23 du cahier des charges : budget et coûts mensuels acceptés, personnes responsables de l'exploitation, détenteur du nom de domaine, de la base et des comptes des services tiers.
- Démarche auprès de l'ARTCI pour les données hébergées hors de Côte d'Ivoire (loi n° 2013-450).
- Périmètre, dates, canal de signalement et critères de réussite (sections 3, 6 et 7).
