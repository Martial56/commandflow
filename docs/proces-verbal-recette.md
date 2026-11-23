# ComandFlow — Procès-verbal de recette

Document prévu au §24 du cahier des charges (« plan de tests et procès-verbal de recette basé sur la section 22 »). À remplir pendant la séance de recette avec Mie Doré, puis à signer.

## 1. Identification

| | |
| --- | --- |
| Projet | ComandFlow — gestion des commandes, de la production, des livraisons, de la facturation et des encaissements |
| Client pilote | Mie Doré |
| Version recettée | commit `……………` (tag `……………`) |
| Environnement | préparation (`preprod.……………`) |
| Date de la séance | ……/……/2026 |
| Lieu | …………………………… |

**Participants**

| Nom | Fonction | Rôle dans ComandFlow | Présent |
| --- | --- | --- | --- |
| | Direction Mie Doré | Direction | ☐ |
| | Gestionnaire | Gestionnaire | ☐ |
| | Préparation | Préparation | ☐ |
| | Chauffeur | Chauffeur | ☐ |
| | Facturation / Comptabilité | Facturation, Comptabilité | ☐ |
| | Développeur | — | ☐ |

## 2. Méthode

Chaque scénario du §22 est vérifié deux fois :

1. **Automatiquement**, dans un vrai navigateur (Chromium), par la commande `pytest -m recette`. Elle tourne aussi à chaque envoi de code, dans le job « Recette » de la CI. Le résultat est relevé au commit recetté.
2. **À la main, par Mie Doré**, sur l'environnement de préparation, avec ses propres comptes et un jeu de données proche de la réalité (vrais produits, quelques vrais clients).

Un scénario est **conforme** quand le critère d'acceptation est observé sans contournement. Toute différence est notée en **réserve** (section 4), avec sa gravité :
- **bloquante** : empêche le pilote ;
- **majeure** : à corriger pendant le pilote ;
- **mineure** : confort, libellé.

## 3. Scénarios du §22

La numérotation reprend celle de `docs/cahier-des-charges.md` (§22, points 13 à 26).

| N° | Critère d'acceptation (§22) | Test automatisé (`recette/`) | Résultat automatique | Vérification Mie Doré | Observations |
| --- | --- | --- | --- | --- | --- |
| 13 | Une commande est créée, reçoit un numéro unique et conserve l'identité de son créateur | `test_commandes_production.py::test_22_13_commande_saisie_a_l_ecran` | ☐ OK ☐ KO | ☐ Conforme ☐ Non conforme | |
| 14 | Une commande récurrente reste en attente jusqu'à la confirmation quotidienne | `test_commandes_production.py::test_22_14_recurrentes_en_attente_jusqu_a_confirmation` | ☐ OK ☐ KO | ☐ Conforme ☐ Non conforme | |
| 15 | Plusieurs commandes sont regroupées par produit et date ; les quantités produites sont cumulées sans écraser les saisies précédentes | `test_commandes_production.py::test_22_15_16_plan_regroupe_et_fournees_cumulees` | ☐ OK ☐ KO | ☐ Conforme ☐ Non conforme | |
| 16 | Le responsable visualise les écarts entre quantités nécessaires, produites et disponibles | idem | ☐ OK ☐ KO | ☐ Conforme ☐ Non conforme | |
| 17 | Le préparateur coche une commande contrôlée, puis le chauffeur confirme personnellement qu'il l'a reçue | `test_chargement.py::test_22_17_18_25_du_chargement_au_depart` | ☐ OK ☐ KO | ☐ Conforme ☐ Non conforme | |
| 18 | Le système conserve l'heure, les deux identités et les éventuels écarts de chargement ; un départ ne peut être déclaré complet sans confirmation ou exception approuvée | idem | ☐ OK ☐ KO | ☐ Conforme ☐ Non conforme | |
| 19 | Le chauffeur confirme la livraison, renseigne les quantités réelles et ajoute une preuve choisie parmi les moyens activés | `test_livraison.py::test_22_19_20_21_livraisons_depuis_le_telephone` | ☐ OK ☐ KO | ☐ Conforme ☐ Non conforme | |
| 20 | Une livraison partielle exige un accord client tracé et le reliquat est conservé | idem | ☐ OK ☐ KO | ☐ Conforme ☐ Non conforme | |
| 21 | Un échec impose un motif ; un retour enregistre quantité, motif et état | idem | ☐ OK ☐ KO | ☐ Conforme ☐ Non conforme | |
| 22 | Après livraison, le service autorisé choisit le workflow FNE ou non-FNE ; la livraison reste enregistrée indépendamment de ce choix | `test_facturation_paiements.py::test_22_22_23_24_de_la_livraison_au_paiement` | ☐ OK ☐ KO | ☐ Conforme ☐ Non conforme | |
| 23 | Une facture ou un document est retrouvable par client, date, numéro et montant | idem | ☐ OK ☐ KO | ☐ Conforme ☐ Non conforme | |
| 24 | Un paiement partiel met à jour le solde sans marquer à tort la totalité comme payée | idem | ☐ OK ☐ KO | ☐ Conforme ☐ Non conforme | |
| 25 | Un chauffeur ne voit pas les tournées d'un autre chauffeur sans autorisation | `test_chargement.py::test_22_17_18_25_du_chargement_au_depart` et `test_livraison.py` | ☐ OK ☐ KO | ☐ Conforme ☐ Non conforme | |
| 26 | Les données d'une entreprise ne sont pas accessibles depuis le compte d'une autre entreprise | `test_isolation.py::test_22_26_rien_de_l_entreprise_a_depuis_l_entreprise_b` | ☐ OK ☐ KO | ☐ Conforme ☐ Non conforme | |

**Vérifications complémentaires** (hors §22, ajoutées pendant la recette) :

| Sujet | Test automatisé | Résultat automatique | Vérification Mie Doré | Observations |
| --- | --- | --- | --- | --- |
| Connexion par profil, code de vérification de la Direction et de la Comptabilité | `test_socle.py` | ☐ OK ☐ KO | ☐ Conforme ☐ Non conforme | |
| Contrôle du chargement : coches rapides, écran conforme à l'enregistrement | `test_chargement.py::test_coches_rapides_l_ecran_reste_conforme_a_l_enregistrement` | ☐ OK ☐ KO | ☐ Conforme ☐ Non conforme | |
| Photo du bordereau réduite sur le téléphone | `test_livraison.py::test_photo_reduite_sur_le_telephone_avant_l_envoi` | ☐ OK ☐ KO | ☐ Conforme ☐ Non conforme | |
| Compte créé par un responsable, mot de passe provisoire remplacé, preuves réglées | `test_parametres.py::test_compte_cree_puis_mot_de_passe_remplace` | ☐ OK ☐ KO | ☐ Conforme ☐ Non conforme | |

## 4. Réserves

| N° | Scénario | Constat | Gravité (bloquante, majeure, mineure) | Correction prévue (date) | Levée le |
| --- | --- | --- | --- | --- | --- |
| R1 | | | | | |
| R2 | | | | | |
| R3 | | | | | |

## 5. Décision

☐ **Recette prononcée sans réserve** : le pilote peut commencer.

☐ **Recette prononcée avec réserves** : le pilote peut commencer ; les réserves majeures sont corrigées pendant le pilote, aux dates indiquées.

☐ **Recette refusée** : les réserves bloquantes sont corrigées, puis une nouvelle séance est fixée au ……/……/2026.

## 6. Signatures

| Pour Mie Doré | Pour le développement |
| --- | --- |
| Nom : | Nom : |
| Fonction : | Fonction : |
| Date : | Date : |
| Signature : | Signature : |
