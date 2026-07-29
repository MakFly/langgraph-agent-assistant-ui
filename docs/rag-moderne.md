# Du RAG vanilla au RAG mesuré

Ce document décrit ce que fait la chaîne de recherche du projet, **et surtout ce
que chaque brique apporte réellement, chiffres à l'appui**. Il n'y a pas ici de
liste de techniques à la mode : il y a un tableau d'ablation, et des lignes qui
ne rapportent rien.

Tout ce qui suit est reproductible :

```bash
make corpus     # régénère corpus/, eval/questions.yaml et mailbox/ (déterministe)
make ingest     # indexe
make eval       # mesure la configuration courante
make ablation   # compare les techniques une par une
make calibrate  # DÉDUIT le seuil d'abstention d'un critère écrit
make inbox-eval # mesure la verticale, sur 5 exécutions
```

---

## 1. Le point de départ n'était pas ce qu'on croyait

Le projet affichait une « recherche hybride dense + lexicale fusionnée par RRF ».
Sur le papier, c'est déjà au-delà d'un RAG vanilla. La mesure a dit autre chose.

**La branche lexicale ne servait à rien.** `websearch_to_tsquery` conjugue les
termes par ET. Sur une question en langue naturelle :

```
« il reste combien à la charge de Bativert en cas de pépin sur la multirisque ? »
        ↓ websearch_to_tsquery('french', …)
'rest' & 'combien' & 'charg' & 'bativert' & 'cas' & 'pépin' & 'multirisqu'
        ↓
0 fragment sur 311
```

Sept lexèmes exigés dans le même fragment : jamais. Mesuré sur le jeu complet, la
branche lexicale seule rendait **1,1 % de rappel**. Aucune erreur, aucun log,
aucune alerte — juste la moitié du système qui ne servait jamais. La « recherche
hybride » était une recherche dense avec du code mort à côté.

La correction bascule l'opérateur en OU sur la forme textuelle de la tsquery, en
laissant `websearch_to_tsquery` faire le découpage, la racinisation et
l'échappement (`retrieve.py`). Le lexical passe de **1,1 % à 61,3 %**.

**Et c'est là que la deuxième surprise arrive.**

---

## 2. Réparer une brique peut dégrader l'ensemble

Une fois le lexical réparé, la fusion hybride est devenue **moins bonne que le
dense seul** : 80,6 % contre 94,6 % de rappel.

La RRF à poids égal en est la cause. Elle est vendue comme « rien à calibrer »,
ce qui est vrai pour l'échelle des scores et faux pour l'importance relative des
listes :

```
fragment premier en lexical, vingtième en dense  →  1/61 + 1/80 = 0,029   ← gagne
fragment premier en dense,   absent  en lexical  →  1/61       = 0,016
```

Un récupérateur faible mais **toujours plein** — une recherche lexicale en OU
trouve quelque chose pour n'importe quelle question — fait descendre les
meilleurs résultats de l'autre. Le poids relatif se calibre donc, et le projet
l'expose (`RAG_SPARSE_WEIGHT`) au lieu de prétendre qu'il n'existe pas.

---

## 3. Ce que le rappel ne voit pas — deux fois plutôt qu'une

La mesure de départ ressemblait à une réussite :

```
rappel@5             94,6 %
MRR                  0,677
abstention correcte   0,0 %   ← 0 sur 35
```

Sur les 35 questions dont la réponse **n'existe nulle part** dans le corpus, le
système a répondu 35 fois. Avec aplomb, en citant des sources, en présentant le
contrat cyber d'un autre client à qui demandait celui d'un client qui n'en a pas.

C'est la panne qui compte en courtage, et le rappel ne la voit pas — par
construction : il mesure ce qu'on trouve, jamais ce qu'on aurait dû ne pas dire.
D'où le jeu d'évaluation : **93 cas positifs et 35 négatifs difficiles**, en trois
familles :

| Famille | Exemple | Ce qu'elle piège |
|---|---|---|
| sujet absent | « Quelle est la politique de télétravail du cabinet ? » | le corpus ne couvre pas le sujet |
| produit non détenu | « Quelle est la franchise cyber de Bativert ? » | Bativert n'a pas de cyber — d'autres clients si |
| entité inexistante | « Où en est le sinistre SIN-2026-0400 ? » | référence plausible, jamais émise |

La deuxième famille est la plus instructive : le client existe, le produit
existe, le document ressemble. Seul un système capable de dire *non* s'en sort.

**Le rappel a un second angle mort.** Il compte les *sources* retrouvées, jamais
le *texte* rendu. Retrouver le bon contrat et servir le fragment d'à côté — celui
qui décrit les parties plutôt que la franchise — donne un rappel parfait et une
réponse impossible. D'où la **couverture du fait** : trente-huit questions
déclarent la valeur exacte que la réponse doit contenir, telle qu'elle est écrite
dans le document, et la mesure vérifie sa présence dans le texte effectivement
rendu.

C'est aussi la seule métrique que puisse faire bouger une technique agissant sur
le texte plutôt que sur le classement :

| | rappel@5 | couverture du fait |
|---|---:|---:|
| sans voisinage | 91,4 % | 89,5 % |
| voisinage = 1 | 91,4 % | **97,4 %** |
| voisinage = 2 | 91,4 % | 97,4 % |

Le rappel ne bouge pas d'un dixième — il ne *peut pas* — pendant que la
couverture gagne huit points. Un tableau d'ablation qui n'aurait affiché que le
rappel aurait conclu que l'élargissement au voisinage ne sert à rien.

---

## 4. L'architecture

```text
╔════════════════════════════════ INDEXATION ════════════════════════════════╗
║ corpus par groupe · parsing · découpage · contexte structurel · embeddings ║
╚═════════════════════════════════════════════════════════════════════════════╝
       │ contenu + ACL + métadonnées
       ▼
┌──────────────────┐  fragments contextualisés  ┌──────────────────────┐
│ parse + chunking │────────────────────────────▶│ modèle d'embedding   │
└──────────────────┘                             └──────────┬───────────┘
                                                         │ vecteurs 1536
                                                         ▼
╔══════════════════════════════ INDEX RAG ════════════════════════════════════╗
║ PostgreSQL · pgvector HNSW · tsvector GIN · ACL GIN · métadonnées JSONB    ║
╚═════════════════════════════════════════════════════════════════════════════╝
```

**Légende indexation.** Le corpus fournit le texte, les groupes ACL et les
métadonnées métier. Le préfixe contextuel est structurel et reste invisible dans
le passage finalement cité.

```text
╔════════════════════════════════ RECHERCHE ══════════════════════════════════╗
║ profil modern-hyde-v1 · question + groupes ACL + filtres métier             ║
╚═════════════════════════════════════════════════════════════════════════════╝
       │ question réelle
       ├──── reformulation LLM ────▶┌───────────────────────┐
       │                            │ formulations réelles  │
       │                            └───────────┬───────────┘
       │                                        ├──── embeddings requête ──▶┌─────────────┐
       │                                        │                           │ dense HNSW  │
       │                                        └──── texte réel ─────────▶┌┴────────────┐
       │                                                                    │ lexical GIN │
       └──── génération HyDE ─────▶┌────────────────────────┐               └──────┬──────┘
                                    │ document hypothétique │                      │ candidats
                                    └───────────┬────────────┘                      │
                                                │ embedding document + moyenne     │
                                                └─────────────────────▶ dense HNSW │
                                                                                   ▼
                                              ┌─────────────────────────────────────┐
                                              │ fusion RRF pondérée · ACL dans SQL  │
                                              └─────────────────┬───────────────────┘
                                                                │ vivier top 20
                                                                ▼
                                              ┌─────────────────────────────────────┐
                                              │ reranking LLM · score 0 à 10        │
                                              └─────────────────┬───────────────────┘
                                                                │ passages notés
                                                                ▼
                                              ┌─────────────────────────────────────┐
                                              │ seuil 4,5 · voisinage small-to-big  │
                                              └─────────────────┬───────────────────┘
                                                                │ sources vérifiables
                                                                ▼
                                              SearchResult(passages, abstention)
```

**Légende recherche.** `HyDE` génère un pivot sémantique, pas une réponse :
son texte ne passe jamais dans la branche lexicale, les citations ni la sortie.
`ACL dans SQL` signifie que les candidats interdits ne quittent jamais la base.
Les appels auxiliaires échouent ouverts : leur panne ramène à la question réelle
au lieu de vider la recherche.

**Composants.** `hyde.py` génère les pivots · `query.py` reformule ·
`retrieve.py` orchestre dense, lexical, RRF, seuil et voisinage · `rerank.py`
note les candidats · PostgreSQL porte l'index et l'isolation ACL.

---

## 5. Les briques, et ce que chacune coûte

| Brique | Où | Coût par recherche | Ablatable à chaud |
|---|---|---|---|
| Recherche dense (pgvector HNSW) | `retrieve.py` | 1 embedding par formulation | oui |
| Recherche lexicale (tsvector fr) | `retrieve.py` | 0 appel modèle | oui |
| Fusion RRF **pondérée** | `retrieve.py` | 0 appel modèle | oui |
| Filtres métier et ACL | `retrieve.py` | filtres SQL | oui |
| Expansion multi-requête | `query.py` | 1 appel LLM + recherches supplémentaires | oui |
| **HyDE** | `hyde.py` | n appels LLM + n embeddings document | oui |
| Reclassement par liste | `rerank.py` | 1 appel LLM | oui |
| Seuil d'abstention | `retrieve.py` | 0 appel modèle | oui |
| Voisinage (small-to-big) | `retrieve.py` | n lectures SQL | oui |
| Indexation contextuelle | `ingest.py` | structurelle | **non** — réindexation |
| Taille de fragment | `chunk.py` | — | **non** — réindexation |

HyDE et multi-requête ne sont pas des synonymes. La multi-requête cherche
plusieurs formulations de la question. HyDE écrit un document qui ressemblerait
à une bonne source, l'encode avec l'encodeur de documents, puis utilise la
moyenne vectorielle comme pivot. Le profil inclut aussi le vecteur de la
question dans cette moyenne.

Les deux dernières lignes conditionnent ce qui a été **vectorisé** : les comparer
impose de réindexer. L'ingestion le détecte seule (`index_profile` en base) et
refait les documents concernés.

---

## 6. Les mesures actuelles

156 documents · 313 fragments · 128 questions (93 positives, 35 négatifs
difficiles) · `text-embedding-3-small` · auxiliaires par `gpt-5.6-luna`.

### Apport isolé de HyDE

Le test A/B conserve le même hybride pondéré et ne change que HyDE :

| Configuration | rappel@5 | couverture | MRR | exactitude |
|---|---:|---:|---:|---:|
| hybride pondéré 0,3 | 91,4 % | 89,5 % | 0,636 | 66,4 % |
| hybride pondéré 0,3 + HyDE ×1 | **93,5 %** | **92,1 %** | **0,687** | **68,0 %** |

HyDE apporte donc bien un gain sur ce corpus ; il n'est pas seulement présent
dans le code. Son intérêt est surtout de rapprocher une question familière de
documents rédigés dans le vocabulaire contractuel.

### Profil de production `modern-hyde-v1`

Le profil versionné active d'un bloc : hybride 0,3, multi-requête ×3, HyDE ×1,
reranking LLM sur 20 candidats, seuil 4,5 et voisinage 1. Trois exécutions
indépendantes complètes donnent :

| Run | rappel@5 | couverture | MRR | abst. correcte | abst. abusive | exactitude | fuite ACL |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 98,9 % | 100,0 % | 0,822 | 77,1 % | 0,0 % | 93,0 % | 0 |
| 2 | 97,8 % | 97,4 % | 0,811 | 77,1 % | 1,1 % | 92,2 % | 0 |
| 3 | 96,8 % | 94,7 % | 0,794 | 80,0 % | 1,1 % | 92,2 % | 0 |
| **moyenne** | **97,8 %** | **97,4 %** | **0,809** | **78,1 %** | **0,7 %** | **92,5 %** | **0** |

Ces plages sont plus honnêtes qu'un meilleur run isolé : HyDE échantillonne à
température 0,7 et les services LLM ne sont pas déterministes. La recherche et
la couverture restent hautes ; la frontière autour du seuil explique
l'essentiel de la petite variance.

Le tableau d'ablation complet reste disponible avec `make ablation`. Il inclut
désormais deux lignes finales, « tout cumulé sans HyDE » et « tout cumulé +
HyDE », afin que l'effet ne soit jamais confondu avec celui du reranking ou de
la reformulation.

### Validation bout en bout

Le parcours réel a été vérifié dans l'interface avec le compte public :

- une question décennale appelle `document_search` et non Wikipédia, rend la
  bonne conclusion et cite deux CG accessibles ;
- une question sans réponse déclenche `abstention=True` et n'ajoute aucune
  extrapolation non sourcée ;
- une question dont la réponse existe seulement dans `gestion/` ne révèle ni le
  document ni son montant au compte public ;
- les sorties internes de HyDE, de reformulation et de reranking sont exclues du
  stream UI : seul le résultat final de l'outil atteint la conversation.

---

## 7. Le seuil se déduit, il ne se choisit pas

`make calibrate` balaie le seuil d'abstention de 0 à 10 et rend la courbe
complète. **Une seule passe de reclassement suffit** : le seuil n'agit qu'après
la notation, donc rejouer chaque valeur sur les mêmes notes donne exactement le
résultat de vingt-et-une exécutions, sans en payer le coût ni en subir la
variance.

Avec le profil complet `modern-hyde-v1` et `gpt-5.6-luna`, la calibration retient
**4,5** selon le critère : exactitude globale maximale, puis seuil le plus bas à
égalité. Sur la passe de calibration qui a fixé cette version, ce point donnait
96,8 % de rappel, 77,1 % d'abstention correcte, 3,2 % d'abstention abusive et
91,4 % d'exactitude.

La validation indépendante est meilleure que la passe de calibration et reste
stable sur trois runs : 92,2–93,0 % d'exactitude et 0–1,1 % d'abstention abusive.
Cela ne transforme pas 4,5 en constante universelle : cela montre seulement que
le seuil généralisait correctement au jeu actuel.

**Cette valeur vaut pour CE corpus et CE modèle de reclassement.** Elle se
recalcule ailleurs par la même commande — c'est tout l'intérêt d'un critère écrit
plutôt que d'un nombre codé en dur.

---

## 8. Ce que le tableau d'ablation ne mesure pas

Deux limites, écrites ici pour qu'on ne les lise pas comme des résultats.

1. **La qualité rédactionnelle de la réponse n'est pas mesurée.** La couverture
   du fait vérifie que le texte rendu *contient* la réponse ; elle ne dit pas si
   le modèle la reformule correctement. Un système qui reçoit le bon passage et
   le résume de travers obtiendrait les mêmes chiffres. Côté verticale, en
   revanche, le brouillon est contrôlé : aucune référence étrangère au dossier
   n'y est tolérée, et le contrôle est déterministe.
2. **Un corpus synthétique reste synthétique.** Les documents sont plausibles et
   les questions écrites dans un vocabulaire délibérément différent du leur, mais
   ce ne sont pas de vraies questions de vrais collaborateurs. **C'est la seule
   limite de cette liste qu'aucun code ne peut lever** : elle ne tombera qu'avec
   un corpus réel. Les écarts *relatifs* entre configurations restent bien plus
   fiables que les valeurs absolues — et c'est sur eux que reposent toutes les
   décisions prises ici.

---

## 9. Réglages

| Variable | Défaut | Effet |
|---|---|---|
| `RAG_PROFILE` | `custom` | `custom` ou profil atomique `modern-hyde-v1` |
| `EMBEDDING_PROVIDER` | `openai` | `openai` · `ollama` · `hash` — registre extensible |
| `EMBEDDING_MODEL` | selon fournisseur | modèle d'embedding |
| `EMBEDDING_DIM` | `1536` | figé dans le schéma ; changer impose de réindexer |
| `RAG_CHUNK_TOKENS` | `300` | taille de fragment — réindexation |
| `RAG_CONTEXTUAL` | `0` | préfixe contextuel vectorisé — réindexation |
| `RAG_DENSE` / `RAG_SPARSE` | `1` / `1` | branches actives |
| `RAG_SPARSE_WEIGHT` | `0.3` | poids du lexical dans la RRF |
| `RAG_CANDIDATES` | `30` | vivier par branche |
| `RAG_TOP_K` | `5` | fragments rendus |
| `RAG_MULTI_QUERY` | `0` | reformulations générées |
| `RAG_HYDE_DOCUMENTS` | `0` | documents hypothétiques générés, maximum 8 |
| `RAG_HYDE_INCLUDE_QUERY` | `1` | inclut la question dans la moyenne vectorielle |
| `RAG_HYDE_TEMPERATURE` | `0.7` | diversité des hypothèses ; bornée de 0 à 2 |
| `RAG_RERANK` | `none` | `none` · `llm` |
| `RAG_MIN_RERANK_SCORE` | *(vide)* | seuil d'abstention sur 10 |
| `RAG_LLM_PROVIDER` / `RAG_LLM_MODEL` | *(chat)* | modèle des tâches auxiliaires |

Un profil nommé est atomique : les anciennes variables `RAG_*` laissées dans
un `.env` ne peuvent pas en désactiver silencieusement une moitié. Seuls les
réglages d'indexation, qui nécessitent une réindexation, restent indépendants.

Un seuil absent (`RAG_MIN_RERANK_SCORE` vide) n'est pas un seuil à zéro : c'est
« aucun seuil n'a été calibré ». La distinction compte quand on relit une mesure
six mois plus tard.
