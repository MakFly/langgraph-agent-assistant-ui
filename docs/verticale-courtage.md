# Verticale courtage : du courriel au dossier préparé

Une couche d'intelligence opérationnelle **au-dessus** de la messagerie et de
l'index documentaire. Pas un CRM, pas une GED, pas un logiciel de courtage.

```
Outlook / Gmail  ──▶  cette couche  ──▶  logiciel de courtage / GED
```

La promesse tient en une phrase : **réduire le temps entre le courriel reçu et le
dossier correctement traité.** Rien de plus, et surtout rien qui décide à la
place du collaborateur.

```bash
make inbox       # traite mailbox/ et affiche la file de travail
make inbox-eval  # mesure sur 5 exécutions : moyenne et étendue
```

---

## Ce que le système fait — et ce qu'il ne fera pas

| Fait | Ne fait pas |
|---|---|
| Qualifie la demande (8 intentions) | Dire si une garantie joue |
| Évalue l'urgence sur le délai contractuel | Annoncer une indemnisation |
| Rattache au client et au contrat | Recommander un contrat |
| Détecte les pièces manquantes | Modifier une police |
| Rédige un brouillon | **Envoyer quoi que ce soit** |
| Signale ce qu'il ne sait pas trancher | Deviner quand il hésite |

La colonne de droite n'est pas de la prudence de POC. Un intermédiaire
d'assurance répond personnellement de son devoir de conseil : un système qui
répondrait seul sur l'étendue d'une garantie engagerait le cabinet sur une
position qu'aucun humain n'a validée.

---

## L'enchaînement

```
   mailbox/*.eml
        │
        ▼
┌───────────────────┐  en-têtes · corps décité · pièces jointes lues
│ message.py        │  ← les citations sont retirées : sinon le classifieur
└─────────┬─────────┘    qualifie l'ANCIENNE demande du fil
          ▼
┌───────────────────┐  intention · urgence · entités          ┌──────────┐
│ classify.py       │◀─────────────────────────────────────────│   LLM    │
└─────────┬─────────┘  urgence = délai qui court, pas le ton   └──────────┘
          │
          │   hors_perimetre ──────────────▶ ⚪ classé, aucun appel de plus
          ▼
┌───────────────────────────────────────────────────────────────────────┐
│ link.py — cascade, du certain vers le probable                        │
│                                                                        │
│   1. référence de contrat citée ........................ 0,98         │
│   2. adresse d'expéditeur connue ....................... 0,95         │
│   3. domaine de messagerie (jamais un domaine public) .. 0,80         │
│   4. SIREN reconnu, y compris en pièce jointe .......... 0,90         │
│   5. sémantique + corroboration d'identité ............. 0,45         │
│   6. rien de tout ça → on ne rattache pas                             │
└─────────┬─────────────────────────────────────────────────────────────┘
          │  confiance < 0,60  ou  contrat ambigu ──▶ 🟡 à valider
          ▼
┌───────────────────────────────────────────────────────────────────────┐
│ missing.py — ÉNUMÉRER puis COCHER, jamais rédiger                     │
│                                                                        │
│   référentiel du corpus, chargé ENTIER par filtre de métadonnées       │
│         ↓ découpé en sections énumérées, une par type de demande       │
│   le modèle choisit UNE section et coche ses pièces par leur indice    │
│         ↓ les intitulés sont relus DEPUIS le référentiel               │
│   → réclamer une pièce inexistante est impossible, pas improbable      │
└─────────┬─────────────────────────────────────────────────────────────┘
          ▼
┌───────────────────────────────────────────────────────────────────────┐
│ draft.py — le bon client, le bon contrat, AUCUN dossier antérieur     │
│                                                                        │
│   déclarations de sinistre · rapports d'expertise · fils antérieurs   │
│         → écartés du contexte : ils parlent d'un AUTRE événement       │
│   contrats · avenants · conditions générales · procédures             │
│         → conservés : ce sont eux qui portent franchises et délais     │
└─────────┬─────────────────────────────────────────────────────────────┘
          ▼
   🔴 urgent · 🟠 pièce manquante · 🟢 prêt · 🟡 à valider · ⚪ hors périmètre
```

**L'ordre n'est pas négociable.** Le rattachement précède la recherche de pièces
et le brouillon, parce que les deux dépendent du client. La qualification précède
le rattachement, parce que l'intention oriente le choix du contrat quand le
client en a plusieurs.

**Les groupes de l'appelant traversent toute la chaîne.** Un traitement
automatique n'est pas une raison de lire ce que l'utilisateur n'a pas le droit de
lire — c'est même le moment où la règle est le plus facile à oublier, puisque
personne ne regarde le résultat au moment où il est produit.

---

## Le rattachement est le vrai sujet

Trois issues, et elles ne se valent pas :

- **exact** — le bon client, ou une abstention là où il n'y avait rien à rattacher ;
- **manqué** — pas de rattachement alors qu'il y en avait un. Le collaborateur
  fait le travail à la main, comme avant. Coûteux, rien de cassé ;
- **erroné** — rattachement au **mauvais** client. Le brouillon citera son
  contrat, sa franchise, son sinistre. Et il aura l'air parfaitement crédible.

Les additionner dans un « taux de réussite » masquerait la seule chose qu'on a
besoin de savoir. Le chiffre à surveiller n'est pas l'exactitude : c'est le
nombre d'erronés, qui doit rester nul.

### Le bug que la mesure a trouvé

Un prospect charcutier écrit pour un devis multirisque. Aucune référence, aucune
adresse connue, aucun SIREN : la cascade tombe sur l'étape sémantique. Ses
documents ressemblent à ceux d'une boulangerie du portefeuille — même métier de
bouche, mêmes laboratoires, même vocabulaire — et la boulangerie l'emporte
largement au vote.

Rattachement confiant, et faux. C'est-à-dire la pire sortie possible.

La correction est de fond : **la similarité sémantique dit de quoi on parle, pas
à qui on parle.** L'étape 5 exige désormais une *corroboration d'identité* — au
moins un fragment du nom ou du domaine du candidat doit apparaître dans le
message. Le vote propose, l'identité confirme. Sans elle, on ne rattache pas.

| | avant | après |
|---|---|---|
| rattachement client exact | 14/15 | **15/15** |
| rattachement **erroné** | **1** | **0** |

Les deux cas `semantique` légitimes continuent de passer : « le restaurant de La
Baule » corrobore `lesablier-labaule.fr`, une signature « FERMETAL INDUSTRIE »
corrobore `fermetal`.

---

## Mesure sur la boîte de démonstration

15 courriels, difficulté de rattachement **graduée exprès**, vérité terrain en
en-tête `X-Attendu-*` (retirée du texte soumis au modèle).

| Difficulté | Ce qui est disponible | Cas | Exact |
|---|---|---|---|
| `reference` | la référence du contrat est écrite | 2 | 2 |
| `domaine` | rien qu'une adresse connue | 5 | 5 |
| `siren` | le numéro, parfois dans une pièce jointe | 2 | 2 |
| `semantique` | ni référence, ni adresse — le contenu seul | 2 | 2 |
| `ambigu` | plusieurs contrats possibles | 2 | 2 |
| `inconnu` | l'expéditeur n'est pas au portefeuille | 2 | 2 |

**Un tirage unique ne veut rien dire.** Un modèle à température zéro n'est pas
déterministe : deux exécutions identiques de cette boîte ont donné 15/15 puis
13/15 sur la qualification, sans qu'une ligne de code ait bougé. Sur quinze
courriels, un cas vaut près de sept points. `make inbox-eval` exécute donc le lot
**cinq fois** et rend la moyenne ET l'étendue — c'est l'étendue qui dit combien
on peut se fier à la moyenne.

| Métrique | moyenne | min | max | étendue |
|---|---:|---:|---:|---:|
| rattachement client exact | 15,0 / 15 | 15 | 15 | **0** |
| rattachement client **ERRONÉ** | 0,0 | 0 | **0** | 0 |
| rattachement contrat exact | 11,0 / 15 | 11 | 11 | **0** |
| rattachement contrat **ERRONÉ** | 0,0 | 0 | **0** | 0 |
| intention exacte | 14,8 / 15 | 14 | 15 | 1 |
| section du référentiel exacte | 9,8 / 10 | 9 | 10 | 1 |
| pièces exigées réclamées | 25,6 / 27 | 24 | 26 | 2 |
| pièces réclamées à tort | 3,2 | 2 | 6 | 4 |
| pièces **INVENTÉES** | 0,0 | 0 | **0** | 0 |
| brouillons **CONTAMINÉS** | 0,0 | 0 | **0** | 0 |

**Les trois lignes en gras se lisent sur leur MAXIMUM, pas sur leur moyenne.**
Une faute grave commise une fois sur cinq exécutions reste une faute grave ; une
moyenne de 0,2 ne rassure personne. Les autres se lisent sur la moyenne.

**Lecture honnête :**

- Les 4 contrats non rattachés sont les cas `ambigu`, **correctement envoyés en
  validation**. Zéro erroné sur cinq exécutions : le système ne devine pas.
- 9 dossiers sur 15 passent par un humain. C'est beaucoup, et c'est voulu : sur
  un métier réglementé, un système qui envoie trop en validation se corrige en
  réglant un seuil ; un système qui se trompe silencieusement se corrige en
  perdant un client.
- **Ce qui reste imparfait** est la précision des pièces : 2 à 6 pièces réclamées
  alors qu'elles étaient déjà au dossier. C'est un aller-retour inutile, pas une
  erreur de fond — et le biais va dans le bon sens, sur-réclamer coûtant moins
  cher que laisser partir un dossier incomplet.

### Ce que ces chiffres ont d'abord caché

Le contrôle de contamination annonçait `0` avant même que le brouillon soit
corrigé. La raison : son expression régulière était **doublement échappée** et ne
pouvait rien reconnaître. C'est un test unitaire — vérifiant que le détecteur se
déclenche bien sur un brouillon contaminé — qui l'a révélé. Une métrique à zéro
qu'aucun test ne fait passer au rouge n'est pas un résultat, c'est un angle mort.

---

## Le référentiel n'est pas une base de plus

Le portefeuille — qui sont les clients, quels contrats, quelles adresses — est
reconstruit à la volée depuis les métadonnées de l'index documentaire
(`registry.py`). Il n'y a **pas** de seconde table.

C'est délibéré : dupliquer le référentiel créerait deux sources de vérité qui
divergeraient à la première évolution, et personne pour savoir laquelle a raison.
La conséquence assumée est qu'un client sans contrat indexé n'existe pas pour le
rattachement. Sur un vrai déploiement, le référentiel viendrait du logiciel de
courtage et `registry.py` deviendrait un adaptateur — la forme ne changerait pas.

De même, le **référentiel des pièces exigibles** vit dans le corpus
(`public/procedures/pieces-par-demande.md`), pas dans le code. C'est le
collaborateur, pas le développeur, qui sait qu'une compagnie réclame désormais
une pièce de plus.

---

## Ce qui n'est pas fait, et pourquoi

- **Aucune connexion Microsoft Graph.** OAuth Azure AD, consentement admin,
  webhooks, delta sync : deux à trois semaines qui n'apprennent rien sur le cœur
  du problème. La boîte est un dossier de `.eml`, ce qui suffit à tout mesurer.
  L'entrée est une frontière : y brancher Graph ne change que `read_mailbox`.
- **Aucune écriture.** Pas d'envoi, pas de mise à jour de CRM, pas de création de
  tâche. La sortie est une file de travail, pas une file d'actions.
- **Aucune donnée réelle.** Clients, compagnies, sinistres, courriels : tout est
  inventé et généré de façon déterministe (`corpus-gen/`). Aucune question RGPD
  ne se pose tant que le POC n'a pas vu une vraie boîte — et elle se posera
  sérieusement le jour où il en verra une.
- **Le brouillon ne peut plus parler d'un dossier antérieur — au prix d'un
  renoncement.** Les déclarations de sinistre, rapports d'expertise et fils de
  courriels sont désormais écartés de son contexte, et un contrôle déterministe
  vérifie qu'aucune référence étrangère au courriel n'y apparaît. On perd la
  capacité de dire « comme lors de votre précédent sinistre » : rattacher un
  courriel au bon *dossier* est un travail en soi, et tant qu'il n'est pas fait,
  mieux vaut un brouillon qui n'en parle pas qu'un brouillon qui parle du mauvais.
