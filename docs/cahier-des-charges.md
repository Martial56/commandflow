**COMANDFLOW**

CAHIER DES CHARGES  
FONCTIONNEL ET TECHNIQUE

Gestion des commandes • Production • Livraisons • Facturation •
Encaissements

**Projet pilote : MIE DORÉ**

Version 1.1 — Document de travail pour réunion avec le développeur  
12 octobre 2026

# 1. Présentation et objectif du projet

ComandFlow est une application de gestion opérationnelle destinée aux
entreprises qui reçoivent des commandes, produisent ou préparent des
marchandises, organisent leur livraison, puis gèrent les documents de
facturation et les paiements. Mie Doré est le premier cas d’usage
pilote. L’application devra être conçue de façon configurable afin de
pouvoir, à terme, servir d’autres boulangeries, distributeurs,
grossistes, fournisseurs alimentaires ou entreprises de livraison.

Ce document sert de base de discussion entre le porteur du projet et le
développeur. Les éléments retenus à partir des besoins exprimés sont des
hypothèses de cadrage : les procédures propres à Mie Doré devront être
confirmées avant le développement définitif.

# 2. Objectifs métier

- Centraliser les commandes reçues par WhatsApp, téléphone, en personne,
  par les commerciaux et par bons de commande écrits.

- Réduire les oublis, les erreurs de saisie, les écarts de quantité et
  les contestations entre équipes.

- Regrouper les besoins de production et enregistrer progressivement les
  quantités réellement produites.

- Tracer précisément le transfert de responsabilité entre production,
  préparation et chauffeur.

- Organiser les tournées et confirmer les livraisons à l’aide de preuves
  vérifiables.

- Suivre les factures, les paiements, les échéances et les créances sans
  confondre ces informations avec le statut de livraison.

- Offrir des interfaces adaptées aux rôles sur ordinateur et sur
  téléphone.

- Construire un produit réutilisable et commercialisable auprès d’autres
  entreprises.

# 3. Contexte et hypothèses de départ

| **Élément**                        | **Hypothèse de travail**                                                                  | **À confirmer** |
|------------------------------------|-------------------------------------------------------------------------------------------|-----------------|
| Entreprise pilote                  | Mie Doré                                                                                  | Oui             |
| Volume                             | Environ 50 à 150 commandes par jour                                                       | Oui             |
| Commandes                          | Ponctuelles et récurrentes                                                                | Oui             |
| Saisie                             | Plusieurs employés peuvent enregistrer des commandes                                      | Oui             |
| Production                         | Production groupée, avec saisie progressive des quantités produites                       | Oui             |
| Livraison                          | Flotte interne et prestataires externes                                                   | Oui             |
| Clients                            | Restaurants, supermarchés, particuliers, revendeurs/grossistes, entreprises et événements | Oui             |
| Portefeuille                       | Environ 51 à 200 clients                                                                  | Oui             |
| Identification actuelle            | Nom uniquement ; l’application devra attribuer un identifiant interne unique              | Oui             |
| Catalogue et tarifs                | Catalogue standard et prix unique par produit, sous réserve d’exceptions                  | Oui             |
| Récurrence                         | Les commandes récurrentes doivent être confirmées chaque jour                             | Oui             |
| Modifications                      | À tout moment, après validation par une personne habilitée                                | Oui             |
| Urgences                           | Acceptées selon les capacités disponibles                                                 | Oui             |
| Livraison partielle / substitution | Accord du client obligatoire et accord à conserver                                        | Oui             |
| Facturation                        | Souvent après livraison ; le processus exact doit être validé                             | Oui             |
| Modes de paiement                  | Bon à payer fin de mois, virement, chèque, espèces, Mobile Money                          | Oui             |

# 4. Principes de conception

- Une commande, une production, une livraison, une facture et un
  paiement sont des objets distincts, reliés entre eux mais avec des
  statuts séparés.

- Chaque action importante est datée et associée à l’utilisateur qui l’a
  effectuée.

- Une donnée confirmée ne doit pas pouvoir être modifiée silencieusement
  : toute correction sensible doit être historisée.

- Les écrans affichés dépendent du rôle de l’utilisateur et de ses
  autorisations.

- Le processus doit rester utilisable sur ordinateur, tablette et
  téléphone, y compris pour les utilisateurs peu à l’aise avec les
  outils numériques.

- La FNE est une option liée à la facture, et non une obligation pour
  chaque livraison ou chaque client.

- Le produit doit pouvoir évoluer sans reconstruire toute l’application.

# 5. Utilisateurs, profils et permissions

Le système doit gérer des comptes individuels. Les rôles ci-dessous sont
des profils de départ ; un administrateur autorisé pourra adapter les
droits par entreprise.

| **Profil**                              | **Fonctions principales**                                                                                  | **Restrictions recommandées**                                                              |
|-----------------------------------------|------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------|
| Directeur / direction                   | Vue globale, indicateurs, rapports, validation des exceptions, consultation des historiques.               | Pas de modification technique ou suppression sans trace.                                   |
| Gestionnaire / administrateur métier    | Gestion quotidienne, clients, commandes, planification, paramétrage métier autorisé.                       | Droits définis par la direction.                                                           |
| Agent de prise de commande / commercial | Créer des commandes, consulter les clients et suivre les commandes selon ses droits.                       | Pas d’accès automatique aux salaires, paramètres système ou données financières sensibles. |
| Responsable de production               | Consulter les besoins, répartir les lots, saisir/valider la production, signaler les écarts.               | Pas de validation de livraison ou paiement sans droit spécifique.                          |
| Agent de préparation / chargement       | Consulter la liste de chargement, vérifier les colis/commandes et confirmer le transfert au chauffeur.     | Ne peut pas modifier la commande commerciale sans autorisation.                            |
| Chauffeur-livreur                       | Voir sa tournée, vérifier le chargement, naviguer, enregistrer les livraisons, incidents et retours.       | Ne voit que ses affectations et les informations nécessaires à la livraison.               |
| Facturation                             | Consulter les commandes/livraisons facturables, joindre et retrouver les factures, choisir FNE ou non-FNE. | Pas de modification des quantités livrées sans procédure de correction.                    |
| Comptabilité / recouvrement             | Enregistrer les règlements, suivre les créances, échéances et relevés clients.                             | Accès financier réservé.                                                                   |
| Prestataire externe                     | Accès limité aux livraisons qui lui sont confiées, si un compte lui est attribué.                          | Aucun accès aux autres clients, commandes ou données internes.                             |

# 6. Parcours complet d’une commande

1.  Réception de la demande et création d’une commande avec numéro
    unique.

2.  Vérification des informations, des produits, des quantités, du prix
    et de la date souhaitée.

3.  Confirmation du client, notamment pour les commandes récurrentes
    quotidiennes.

4.  Validation des changements ou urgences par la personne habilitée.

5.  Intégration au plan de production groupée.

6.  Saisie de la production réellement réalisée et signalement des
    écarts.

7.  Préparation des commandes et constitution de la liste de chargement.

8.  Vérification de chaque commande au moment de son transfert au
    chauffeur.

9.  Livraison au client avec enregistrement de la preuve et des
    quantités réellement remises.

10. Traitement des échecs, retours ou reliquats, si nécessaire.

11. Décision de facturation : facture FNE ou traitement sans FNE selon
    le besoin et les règles applicables.

12. Enregistrement et rapprochement des paiements jusqu’à clôture du
    solde.

# 7. Gestion des clients et des produits

## 7.1 Fiche client

- Attribuer un identifiant interne unique même si le nom est
  l’information actuellement utilisée.

- Prévoir les champs : nom, catégorie de client, téléphone, contacts,
  adresse(s) de livraison, localisation éventuelle, consignes,
  conditions de paiement et notes.

- Permettre plusieurs adresses pour un même client et signaler les
  doublons potentiels.

- Conserver l’historique des commandes, livraisons, factures et
  paiements.

- Prévoir l’import initial depuis Excel/CSV.

À confirmer : les champs obligatoires pour les clients professionnels et
ceux nécessaires à la facturation FNE.

## 7.2 Catalogue

- Code et nom du produit, unité, prix de référence, statut actif/inactif
  et notes de préparation.

- Prix appliqué figé dans chaque commande pour préserver l’historique si
  le catalogue change.

- Possibilité de configurer plus tard plusieurs formats,
  conditionnements ou prix par catégorie de client.

- Une substitution de produit exige l’accord du client et
  l’enregistrement de cet accord.

# 8. Module Commandes

- Créer automatiquement un numéro de commande unique.

- Enregistrer client, date/heure de réception, canal, date et créneau de
  livraison, adresse, lignes de produits, quantités, prix, instructions
  et utilisateur créateur.

- Permettre l’ajout d’un bon de commande écrit, d’une photo ou d’un
  document justificatif.

- Supporter les commandes ponctuelles et les modèles récurrents.

- Pour les commandes récurrentes, générer la commande du jour en attente
  de confirmation ; elle ne doit pas être traitée comme confirmée avant
  validation.

- Autoriser modification ou annulation à tout moment seulement après
  validation d’un responsable, avec auteur, date, motif et impact sur la
  production/livraison.

- Les commandes urgentes déclenchent une alerte et sont acceptées selon
  la disponibilité de production et de livraison.

- Empêcher la suppression silencieuse des commandes déjà engagées ;
  utiliser une annulation tracée.

| **Statut de commande** | **Signification**                                               |
|------------------------|-----------------------------------------------------------------|
| Brouillon              | Saisie en cours.                                                |
| À confirmer            | Informations ou confirmation client en attente.                 |
| Confirmée              | Commande validée.                                               |
| À planifier            | À intégrer au plan de production et de livraison.               |
| En préparation         | Production/préparation en cours.                                |
| Prête au chargement    | Commande prête pour contrôle et chargement.                     |
| En livraison           | Transfert au chauffeur enregistré ; tournée en cours.           |
| Partiellement livrée   | Une partie est remise avec accord du client et reliquat tracé.  |
| Livrée                 | Livraison clôturée selon les quantités et preuves enregistrées. |
| En anomalie            | Échec, litige, retour ou décision requise.                      |
| Annulée                | Commande annulée avec historique et motif.                      |

# 9. Module Production groupée

- Regrouper automatiquement les quantités confirmées par produit et par
  date de livraison ; ajouter le créneau horaire si nécessaire.

- Afficher par produit : quantité commandée, quantité à produire,
  quantité déclarée produite, quantité disponible, quantité manquante et
  quantité réservée aux commandes.

- Permettre au responsable de répartir les lots et de modifier le plan
  avant validation.

- Les équipes de production saisissent les quantités au fur et à mesure
  : produit, lot/session, quantité, date/heure, utilisateur et éventuel
  commentaire.

- Autoriser plusieurs saisies de production pour un même produit et une
  même journée, sans écraser les saisies précédentes.

- Comparer automatiquement les quantités cumulées produites aux besoins
  confirmés.

- Signaler les écarts et alerter le responsable si les quantités
  disponibles ne suffisent pas.

- Ne pas considérer une commande comme prête tant que les quantités
  requises ne sont pas disponibles ou qu’une décision autorisée n’a pas
  été prise.

- Conserver l’historique des corrections et validations.

Choix recommandé : regroupement automatique par produit et date, avec
validation humaine du plan. Les écrans de production doivent être
rapides à utiliser, avec de grandes commandes de saisie sur téléphone ou
tablette.

# 10. Préparation et contrôle du chargement — point de contrôle obligatoire

Cette étape est essentielle pour éviter les contestations entre l’équipe
de préparation et le chauffeur. L’application doit enregistrer
explicitement que chaque commande a été contrôlée et remise au
chauffeur.

- Créer une liste de chargement pour une tournée, avec chaque commande
  et le détail des produits/quantités attendus.

- Le préparateur ou responsable de chargement peut cocher chaque
  commande ou ligne après vérification physique.

- Le chauffeur doit confirmer lui-même, depuis son compte, qu’il a
  contrôlé et reçu chaque commande qui lui est affectée.

- Une commande ne doit pas être marquée « chargée et reçue par le
  chauffeur » sur la seule action du préparateur : la confirmation du
  chauffeur est requise.

- Enregistrer qui a préparé, qui a contrôlé, qui a remis, qui a reçu, la
  date/heure et toute anomalie.

- Si un écart est constaté avant départ, enregistrer la quantité
  manquante ou excédentaire et bloquer la confirmation complète jusqu’à
  résolution ou validation d’une exception.

- À la fin du contrôle, afficher un récapitulatif : nombre de commandes
  attendues, vérifiées, manquantes et reçues par le chauffeur.

- Le chauffeur confirme la fin du chargement avant de démarrer la
  tournée. Le responsable peut consulter l’état de chaque commande en
  temps réel.

- Si le chauffeur conteste une quantité, l’écart est consigné avant le
  départ et validé par un responsable ; aucune modification ne doit
  effacer l’historique.

- Une fois le départ confirmé, l’heure de départ et la liste finale des
  commandes chargées sont conservées.

| **Statut de chargement**             | **Règle**                                                                       |
|--------------------------------------|---------------------------------------------------------------------------------|
| À préparer                           | Commande affectée à une tournée, pas encore contrôlée.                          |
| En vérification                      | Contrôle physique en cours.                                                     |
| Écart constaté                       | Différence de quantité ou commande manquante ; décision requise.                |
| Vérifiée par la préparation          | Préparateur confirme le contrôle.                                               |
| Reçue et confirmée par le chauffeur  | Le chauffeur confirme personnellement la réception.                             |
| Chargement clôturé / départ autorisé | Toutes les commandes requises sont confirmées ou une exception a été approuvée. |

# 11. Tournées et affectation des chauffeurs

- Créer une tournée et y ajouter plusieurs commandes prêtes à charger.

- Affecter la tournée à un chauffeur interne ou à un prestataire externe
  ; associer un véhicule si cette fonction est activée.

- Afficher les adresses, contacts, créneaux, quantités, instructions et
  ordre de passage.

- Autoriser le responsable à modifier l’ordre, réaffecter une commande
  ou ajouter une urgence pendant la tournée.

- Prévoir une proposition d’ordre de passage basée sur les adresses et
  créneaux ; le responsable garde le contrôle final.

- Enregistrer départ, progression, fin de tournée et incidents.

- Le chauffeur voit uniquement ses affectations et les données
  nécessaires à son travail.

- Envisager une optimisation automatique avancée après validation du
  budget et du service cartographique.

# 12. Application chauffeur et preuve de livraison

## 12.1 À l’arrivée chez le client

- Le chauffeur ouvre la commande et vérifie les produits/quantités
  remis.

- Il saisit la quantité réellement livrée par ligne, même si elle
  diffère de la quantité prévue.

- Il indique le statut : livrée, partiellement livrée, refusée, client
  absent, reportée ou autre incident.

- Il enregistre l’heure, le nom du réceptionnaire si disponible et un
  commentaire si nécessaire.

## 12.2 Moyens de preuve configurables

La preuve ne doit pas reposer sur un seul mécanisme. L’entreprise pourra
activer un ou plusieurs moyens selon le type de client et la valeur de
la livraison.

- Photo/capture du bordereau de livraison signé ou tamponné, prise
  directement depuis le téléphone ou importée.

- Signature du réceptionnaire sur l’écran du téléphone.

- Code de confirmation à usage unique communiqué au réceptionnaire, puis
  saisi par le chauffeur ; prévoir une procédure de secours si le client
  ne peut pas fournir le code.

- Nom du réceptionnaire et, si nécessaire, fonction ou service.

- Date et heure enregistrées automatiquement.

- Position GPS au moment de la confirmation, si autorisée.

- Numéro du bordereau ou référence écrite, avec possibilité d’ajouter un
  commentaire.

- Pièce jointe ou photo supplémentaire pour un cas particulier.

Recommandation : pour le MVP, rendre obligatoires le statut, les
quantités réellement livrées, l’heure et l’utilisateur ; proposer la
photo du bordereau ou le nom du réceptionnaire comme preuve principale,
et permettre signature, code et GPS en options. Les exigences exactes
seront définies par type de client.

## 12.3 Échec, livraison partielle et retour

- Un motif est obligatoire en cas d’échec ou de livraison partielle :
  client absent, refus, adresse incorrecte, marchandise manquante,
  retard, problème de véhicule ou autre.

- Une livraison partielle exige un accord client enregistré et le détail
  du reliquat à livrer.

- Un retour enregistre produit, quantité, motif, état (revendable, à
  contrôler, abîmé/à détruire) et décision du responsable.

- Permettre une nouvelle tentative ou un report avec nouvelle
  date/consigne.

- Notifier le responsable et conserver les preuves associées.

- Une correction de facture ou un avoir éventuel relève d’un utilisateur
  habilité et d’une procédure distincte.

# 13. Géolocalisation et cartographie

- Enregistrer les adresses des clients et, si nécessaire, leurs
  coordonnées GPS.

- Permettre l’ouverture d’un itinéraire dans une application de
  navigation.

- Option de suivi du chauffeur pendant la tournée, si validée par
  l’entreprise.

- Afficher l’heure de dernière mise à jour de la position ; une position
  ancienne ne doit jamais être présentée comme actuelle.

- Limiter le suivi à la période professionnelle autorisée, avec
  information des chauffeurs et contrôle des permissions.

- Prévoir une alternative en cas de GPS ou de connexion indisponible.

Recommandation de première version : navigation vers les adresses et
position GPS au moment de la preuve de livraison. Le suivi continu en
temps réel peut être ajouté après validation opérationnelle, financière
et des règles de confidentialité.

# 14. Facturation et choix FNE / non-FNE

La facture normalisée électronique (FNE) ne doit pas être imposée
systématiquement à chaque commande ou client dans le fonctionnement de
ComandFlow. Après livraison et vérification des éléments, l’utilisateur
habilité doit pouvoir choisir le traitement applicable selon les règles
de l’entreprise et les obligations légales.

- Après clôture de la livraison, afficher une étape « Facturation » avec
  un choix explicite : « Facture FNE » ou « Traitement sans FNE / autre
  document », lorsque ce choix est légalement applicable.

- Le choix doit être enregistré avec l’utilisateur, la date et le motif
  éventuel ; il ne doit pas modifier le statut de livraison.

- Si FNE est choisie, permettre d’ouvrir le portail officiel, de générer
  la facture selon le processus autorisé, puis de joindre le PDF ou le
  document officiel obtenu.

- Enregistrer les métadonnées disponibles : numéro FNE, date, montant,
  client, fichier et statut.

- Si FNE n’est pas choisie, permettre d’enregistrer le type de document
  ou le motif prévu par la procédure interne, sans fabriquer une facture
  officielle non conforme.

- Relier chaque document à une ou plusieurs commandes/livraisons et au
  client concerné.

- Rechercher les documents par client, numéro, date, montant et statut ;
  conserver l’historique des versions et corrections.

- Une éventuelle intégration API FNE ne sera étudiée qu’après
  confirmation des conditions d’accès, de certification et
  d’autorisation applicables.

Point de vigilance : l’option « sans FNE » dans l’application est un
choix de workflow, et non une dispense des obligations fiscales. Les
utilisateurs doivent appliquer les obligations légales et les procédures
validées par la comptabilité.

Portail officiel : https://www.fne.dgi.gouv.ci/  
Documentation API à vérifier avant tout projet d’intégration :
https://www.fne.dgi.gouv.ci/documents/FNE-procedureapi.pdf

# 15. Paiements, factures ouvertes et créances

- Distinguer les montants commandés, livrés, facturés, encaissés et
  restant dus.

- Enregistrer les paiements par espèces, virement, chèque, Mobile Money
  et tout mode configuré.

- Enregistrer montant, date, mode, référence, utilisateur et pièce
  justificative éventuelle.

- Gérer les paiements partiels et l’affectation d’un règlement à une ou
  plusieurs factures si la procédure comptable le permet.

- Suivre les bons à payer de fin de mois, les dates d’échéance et les
  retards.

- Afficher le relevé de compte du client, les documents émis, les
  règlements et le solde.

- Prévoir les statuts : non payé, partiellement payé, payé, en retard,
  contesté/à vérifier.

- Exporter les états de créances et de paiements.

# 16. Notifications et tableaux de bord

## 16.1 Alertes

- Commande nouvelle, modifiée, urgente, annulée ou récurrente non
  confirmée.

- Production insuffisante ou commande non prête.

- Écart au chargement, commande non confirmée par le chauffeur ou départ
  non clôturé.

- Livraison non démarrée, en retard, partielle, refusée ou échouée.

- Preuve de livraison manquante.

- Facture à traiter, document manquant ou paiement en retard.

- Résumé quotidien des commandes, productions, livraisons et créances.

## 16.2 Indicateurs

| **Domaine**   | **Exemples d’indicateurs**                                               |
|---------------|--------------------------------------------------------------------------|
| Commandes     | Nombre par jour/semaine/mois, canal, client, produit, statut.            |
| Production    | Quantités commandées, à produire, produites, disponibles et manquantes.  |
| Chargement    | Commandes attendues, vérifiées, reçues par chauffeur, écarts.            |
| Livraisons    | Livrées, partielles, échouées, en retard, retours et preuves manquantes. |
| Chauffeurs    | Tournées assignées, livraisons effectuées, incidents et retards.         |
| Facturation   | À traiter, FNE archivée, documents manquants, montants par période.      |
| Encaissements | Montants encaissés par date et mode.                                     |
| Créances      | Soldes dus, retards, échéances et ancienneté des créances.               |

Les tableaux de bord doivent être filtrables par période, client, statut
et équipe selon les droits. Exports recommandés : Excel/CSV et PDF pour
les états de synthèse.

# 17. Exigences multi-appareils et expérience utilisateur

- L’application doit fonctionner sur ordinateur et téléphone, avec une
  interface responsive adaptée à la taille de l’écran.

- Sur ordinateur : écrans de gestion, tableaux filtrables, rapports,
  préparation de tournée et suivi global.

- Sur téléphone : écrans simples et rapides pour production,
  vérification du chargement, tournée, preuve de livraison et incidents.

- Les utilisateurs doivent voir uniquement les menus et données
  correspondant à leur rôle.

- Prévoir de grands boutons et peu de saisies répétitives pour les
  équipes terrain.

- Prévoir, si possible, un mode hors connexion limité pour le chauffeur,
  avec synchronisation et résolution des conflits à la reconnexion.

- Langue initiale : français ; architecture ouverte à d’autres langues.

# 18. Sécurité, traçabilité et données

- Comptes individuels, authentification sécurisée et révocation des
  accès en cas de départ d’un utilisateur.

- Permissions contrôlées côté serveur et base de données, pas uniquement
  dans l’interface.

- Si ComandFlow devient multi-entreprises, séparation stricte des
  données entre chaque entreprise.

- Journal d’audit pour les changements de commande, validations, écarts
  de chargement, preuves, retours, documents et paiements.

- Stockage privé des documents, bordereaux, photos et signatures, avec
  accès limité aux personnes autorisées.

- Sauvegardes régulières, procédure de restauration et test périodique
  de récupération.

- Protection des clés API et secrets ; environnements de test et de
  production séparés.

- Définir les durées de conservation des documents et des données GPS
  avec la direction.

# 19. Architecture technique indicative

Le développeur proposera une architecture adaptée au budget, à ses
compétences et aux besoins d’évolution. Les choix ci-dessous sont des
pistes et non des contraintes absolues.

| **Composant**    | **Exigence / orientation**                                                             |
|------------------|----------------------------------------------------------------------------------------|
| Interface        | Application web responsive/PWA utilisable sur ordinateur, tablette et téléphone.       |
| Base de données  | Base relationnelle fiable, sauvegardée, avec migrations versionnées.                   |
| Authentification | Gestion sécurisée des comptes, rôles et permissions.                                   |
| Stockage         | Documents privés avec liens d’accès contrôlés et journalisation.                       |
| Cartographie     | Fournisseur de cartes, géocodage et itinéraires à choisir selon coûts/licences.        |
| Notifications    | Notifications dans l’application en priorité, puis e-mail ou intégrations validées.    |
| Exploitation     | Dépôt de code appartenant au projet, documentation, environnements de test/production. |
| Évolutivité      | Configuration par entreprise et séparation des données pour la commercialisation SaaS. |

L’utilisation d’un outil d’aide au développement comme Replit Agent peut
accélérer le travail, mais ne remplace pas les tests, la revue de
sécurité, les sauvegardes, la documentation ni la maîtrise des comptes
de production.

# 20. Modèle de données indicatif

| **Objet**             | **Contenu essentiel**                                                                |
|-----------------------|--------------------------------------------------------------------------------------|
| Entreprise            | Paramètres, identité, options activées.                                              |
| Utilisateur / rôle    | Compte, permissions, entreprise, statut.                                             |
| Client / adresse      | Identifiant, nom, catégorie, contacts, adresses.                                     |
| Produit               | Code, libellé, unité, prix, statut.                                                  |
| Commande / lignes     | Dates, canal, client, quantités, prix, statut, historique.                           |
| Validation / accord   | Action approuvée, personne, date, motif et preuve.                                   |
| Production / lot      | Produit, date, quantité prévue/produite, opérateur.                                  |
| Chargement            | Tournée, commande, contrôle préparateur, confirmation chauffeur, écarts.             |
| Tournée / affectation | Chauffeur, véhicule éventuel, arrêts, statut, heures.                                |
| Livraison / preuve    | Quantités réelles, réceptionnaire, bordereau, photo, code, signature/GPS optionnels. |
| Incident / retour     | Motif, quantité, état du produit, décision.                                          |
| Facture / document    | Client, commande(s), FNE ou autre document, numéro, montant, fichier.                |
| Paiement              | Montant, date, mode, référence, affectation, solde.                                  |
| Notification / audit  | Destinataire, événement, horodatage, action et résultat.                             |

# 21. Priorités de développement

| **Priorité**                   | **Fonctions**                                                                                                                                                                                                                                                                                                                                                                                                        |
|--------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Phase 1 — MVP                  | Utilisateurs et rôles ; clients/produits ; commandes et confirmation quotidienne ; production groupée avec saisie cumulative ; liste de chargement ; double validation préparation/chauffeur ; tournées et affectations ; application chauffeur ; preuve de livraison ; incidents/retours ; choix FNE/non-FNE et archivage de documents ; paiements de base ; tableau de bord essentiel ; exports ; journal d’audit. |
| Phase 2 — Améliorations        | Proposition d’ordre de tournée, géolocalisation à la livraison, alertes avancées, mode hors connexion partiel, import Excel, relevés clients et créances avancés.                                                                                                                                                                                                                                                    |
| Phase 3 — Intégrations et SaaS | Étude API FNE si autorisée, WhatsApp Business officiel, optimisation avancée des tournées, multi-entreprises complet, gestion d’abonnements et facturation SaaS.                                                                                                                                                                                                                                                     |

La priorité est de réussir un parcours complet et traçable avant
d’ajouter des automatisations coûteuses.

# 22. Scénarios de recette et critères d’acceptation

13. Une commande est créée, reçoit un numéro unique et conserve
    l’identité de son créateur.

14. Une commande récurrente reste en attente jusqu’à la confirmation
    quotidienne.

15. Plusieurs commandes sont regroupées par produit et date ; les
    quantités produites sont cumulées sans écraser les saisies
    précédentes.

16. Le responsable visualise les écarts entre quantités nécessaires,
    produites et disponibles.

17. Le préparateur coche une commande contrôlée, puis le chauffeur
    confirme personnellement qu’il l’a reçue.

18. Le système conserve l’heure, les deux identités et les éventuels
    écarts de chargement ; un départ ne peut être déclaré complet sans
    confirmation ou exception approuvée.

19. Le chauffeur confirme la livraison, renseigne les quantités réelles
    et ajoute une preuve choisie parmi les moyens activés.

20. Une livraison partielle exige un accord client tracé et le reliquat
    est conservé.

21. Un échec impose un motif ; un retour enregistre quantité, motif et
    état.

22. Après livraison, le service autorisé choisit le workflow FNE ou
    non-FNE ; la livraison reste enregistrée indépendamment de ce choix.

23. Une facture/document est retrouvable par client, date, numéro et
    montant.

24. Un paiement partiel met à jour le solde sans marquer à tort la
    totalité comme payée.

25. Un chauffeur ne voit pas les tournées d’un autre chauffeur sans
    autorisation.

26. Les données d’une entreprise ne sont pas accessibles depuis le
    compte d’une autre entreprise.

# 23. Points à arbitrer lors de la réunion avec le développeur

- Qui valide les commandes, les modifications urgentes et les
  annulations ?

- Qui peut corriger une quantité de production après validation ?

- Le chargement est-il contrôlé par commande entière, par produit, ou
  les deux ?

- Faut-il obliger la double validation préparateur + chauffeur pour
  toutes les commandes ou seulement pour les tournées internes ?

- Quels moyens de preuve de livraison sont obligatoires selon les
  clients : bordereau photo, signature, code, GPS ou combinaison ?

- Les prestataires externes utiliseront-ils un compte ComandFlow ou
  transmettront-ils leurs preuves au responsable ?

- Le suivi GPS continu est-il réellement nécessaire dès le MVP ?

- Quelles règles comptables et fiscales déterminent le choix FNE ou
  non-FNE pour chaque type de transaction ?

- Quels sont le budget, le délai, les coûts mensuels acceptables et les
  personnes responsables de l’exploitation ?

- Qui détient le code source, le nom de domaine, la base de données et
  les comptes des services tiers ?

# 24. Livrables attendus du développeur

- Maquettes des écrans et validation du parcours utilisateur avant
  réalisation complète.

- Application de test avec comptes par rôle et données de démonstration.

- Code source stocké dans un dépôt contrôlé par le porteur du projet
  selon l’accord conclu.

- Schéma de base de données, documentation des rôles, permissions et
  statuts.

- Plan de tests et procès-verbal de recette basé sur la section 22.

- Procédure de sauvegarde, restauration, déploiement et mise à jour.

- Inventaire des services tiers, coûts récurrents, quotas et conditions
  de résiliation.

- Formation des utilisateurs clés et modalités de correction des
  anomalies après livraison.

# 25. Décision proposée

Avant le développement, valider avec Mie Doré les règles critiques et le
périmètre MVP. Le premier prototype doit couvrir le flux complet :
commande reçue → production enregistrée → commande vérifiée au
chargement par la préparation et le chauffeur → tournée → preuve de
livraison → choix de facturation FNE ou non-FNE selon les règles
applicables → suivi du paiement.

Ce parcours constituera le fil conducteur de la conception, du
développement, des tests et de la démonstration à la direction.

# Annexe — Référence FNE

Portail officiel FNE Côte d’Ivoire : https://www.fne.dgi.gouv.ci/

Documentation API à vérifier auprès des services compétents avant toute
intégration : https://www.fne.dgi.gouv.ci/documents/FNE-procedureapi.pdf

Ce document est un cahier des charges fonctionnel et technique de
travail ; il ne remplace pas la validation des procédures fiscales,
comptables, juridiques ou de protection des données applicables.
