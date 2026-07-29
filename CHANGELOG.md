# Changelog

Toutes les modifications notables de ce projet sont documentées dans ce fichier.

Le format suit [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/) et le
projet utilise le versionnage sémantique.

## [Unreleased]

### Ajouté

- Chaîne RAG moderne mesurable : recherche dense et lexicale, fusion RRF
  pondérée, multi-query, HyDE, reclassement LLM, voisinage de fragments et
  abstention calibrée.
- Profil atomique `modern-hyde-v1`, commandes d'ablation et de calibration, jeu
  d'évaluation enrichi et documentation des métriques.
- Corpus déterministe de courtage IARD, boîte de réception de démonstration et
  pipeline métier de préparation des dossiers.
- Administration des sources dans **Configuration → Sources**, réservée aux
  administrateurs : création, groupes ACL, upload, suppression, simulation,
  synchronisation et historique durable.
- OCR multimodal dynamique par source : provider, nom libre du modèle, prompt,
  résolution et plafond de pages configurables sans redéploiement.
- Extraction hybride des PDF : couche texte native conservée et OCR limité aux
  pages scannées ; support des images PNG, JPEG, WebP et TIFF.
- Volume Docker persistant `ingestion_data` et reprise des jobs interrompus après
  redémarrage.
- Contexte produit `PRODUCT.md` pour cadrer les utilisateurs, la personnalité,
  l'accessibilité WCAG 2.2 AA et les principes de conception.

### Modifié

- L'identité documentaire accepte désormais le même chemin relatif dans plusieurs
  sources grâce à l'unicité `(root, source)`.
- Les changements de configuration OCR font partie du profil d'indexation et
  déclenchent donc une réindexation idempotente.
- L'interface de configuration s'élargit uniquement pour l'administration des
  sources et conserve le langage visuel existant.
- Le corpus de démonstration et les questions d'évaluation couvrent maintenant
  davantage de produits, procédures et cas négatifs difficiles.

### Corrigé

- Routage des questions métier vers `document_search`.
- Fuites de sorties intermédiaires des modèles auxiliaires dans le stream.
- Réponses spéculatives et fausses citations après une abstention du RAG.
- Réponse 500 produite par un JWT valide dont l'utilisateur avait été supprimé.
- Détection de contamination dont la regex doublement échappée ne se déclenchait
  jamais.
- Réexécution concurrente du DDL de l'index RAG pouvant provoquer un deadlock.

### Sécurité

- Séparation stricte entre rôle administrateur et groupes documentaires.
- Routes d'ingestion protégées par le RBAC administrateur.
- Clés des providers conservées côté serveur ; seule leur disponibilité est
  exposée à l'interface.
- Validation des groupes, extensions, noms de fichiers, chemins et tailles
  d'upload.
- Plafonds de fragments et de pages OCR avant les opérations coûteuses.

### Validation

- 212 tests backend réussis.
- Ruff, TypeScript et lint frontend réussis.
- Build frontend de production réussi.
- Parcours navigateur validé : configuration OCR `openai / gpt-5.6-luna`,
  upload texte et image, simulation multimodale et écriture pgvector.

### Limite opérationnelle connue

- Les jobs sont persistants et revendiqués atomiquement, mais restent exécutés
  dans le processus API. Une production horizontalement scalable devra les
  déplacer vers un worker et une file dédiés.
- L'image API actuelle reste une image de développement avec `uvicorn --reload`
  et les dépendances de test ; une image de production demandera un Dockerfile
  multi-stage dédié.
