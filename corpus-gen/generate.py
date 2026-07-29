"""Génère le corpus de démonstration d'un cabinet de courtage, et son jeu d'évaluation.

    python3 corpus-gen/generate.py --out corpus --eval eval/questions.yaml

**Pourquoi générer plutôt qu'écrire à la main.** Il faut un corpus assez grand
pour que les mesures veuillent dire quelque chose : sur quatre documents, tout
classement est bon, et aucune technique de recherche ne se distingue d'une autre.
En dessous de cent-cinquante documents et de soixante questions, un écart de
rappel n'est pas un signal, c'est du bruit d'échantillonnage.

**Deux propriétés valent d'être défendues.**

1. **Déterminisme total.** Une seule graine (`entities.SEED`), aucun appel à
   l'horloge, aucune source d'entropie. Deux exécutions produisent les mêmes
   octets. Sans ça, `make ingest` réindexerait tout à chaque passe, et surtout
   deux mesures ne seraient plus comparables — on ne saurait pas si un écart
   vient de la technique testée ou d'un corpus qui a bougé.

2. **Le vocabulaire des questions diverge de celui des documents.** C'est le
   piège classique de l'évaluation d'un RAG : on écrit les questions en regardant
   les documents, on en reprend les mots, et la recherche lexicale trouve tout.
   Le score est flatteur et faux. Ici, les documents parlent de « franchise
   contractuelle par sinistre » et les questions demandent « il reste combien à
   leur charge ». C'est ce décalage qui rend la mesure honnête — et c'est aussi
   ce qui fait que le score de départ n'est pas de 100 %.

**Un cas sur quatre est un négatif difficile.** Un RAG se juge autant sur ce
qu'il refuse de répondre que sur ce qu'il trouve. Trois familles sont plantées
exprès :

- des questions dont la réponse n'est **nulle part** dans le corpus ;
- des questions qui portent sur un produit que le client cité **n'a pas** — le
  cas où une recherche sans seuil rend, avec assurance, le contrat d'un autre
  client. C'est la panne la plus coûteuse en courtage, et elle est invisible
  tant qu'on ne mesure que le rappel ;
- des questions visant une référence ou un client **qui n'existe pas**, assez
  plausibles pour que la recherche dense trouve toujours « quelque chose ».

Rien ici n'est réel : ni les entreprises, ni les compagnies, ni les sinistres.
"""

from __future__ import annotations

import argparse
import random
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from entities import (  # noqa: E402
    CABINET,
    PIECES_REFERENTIEL,
    CLIENTS,
    CLIENTS_BY_ID,
    COLLABORATEURS,
    COMPAGNIES,
    COMPAGNIES_BY_ID,
    CONTRATS,
    PRODUITS,
    PRODUITS_BY_ID,
    SEED,
    SINISTRES,
    Client,
    Contrat,
    contrats_du_client,
)

# --- Structures ---------------------------------------------------------------


@dataclass
class Doc:
    """Un fichier à écrire. `meta` part en front-matter YAML."""

    path: str  # relatif à la racine du corpus, ACL = premier segment
    meta: dict
    body: str


@dataclass
class Case:
    """Un cas d'évaluation.

    `expect` vide + `abstain` vrai décrit un négatif difficile : la bonne réponse
    est de ne rien affirmer.
    """

    question: str
    groups: list[str]
    expect: list[str] = field(default_factory=list)
    abstain: bool = False
    note: str | None = None
    fact: str | None = None
    """Le FAIT que la réponse doit contenir, tel qu'il est écrit dans le document.

    Retrouver le bon document ne prouve pas qu'on pourra répondre : le fragment
    rendu peut être celui d'à côté, ou coupé juste avant le chiffre. Ce champ
    permet de mesurer la **couverture du fait** — le texte effectivement rendu
    contient-il la réponse ? — qui est la seule métrique sensible aux techniques
    agissant sur le TEXTE plutôt que sur le classement, l'élargissement au
    voisinage en tête."""


DOCS: list[Doc] = []
CASES: list[Case] = []

RNG = random.Random(SEED)

GROUPES = ("public", "production", "gestion", "sinistres", "direction")


def add(doc: Doc) -> None:
    DOCS.append(doc)


def ask(
    question: str,
    groups: list[str],
    expect: list[str],
    note: str | None = None,
    fact: str | None = None,
) -> None:
    CASES.append(Case(question, groups, expect, note=note, fact=fact))


def ask_abstain(question: str, groups: list[str], note: str) -> None:
    CASES.append(Case(question, groups, [], abstain=True, note=note))


def pick(pool) -> str:
    return RNG.choice(list(pool))


def euros(value: int) -> str:
    return f"{value:,}".replace(",", " ") + " €"


def date_fr(iso: str) -> str:
    """ISO en base, jj/mm/aaaa dans le texte lu par un humain."""
    annee, mois, jour = iso.split("-")
    return f"{jour}/{mois}/{annee}"


# --- Conditions générales (public) --------------------------------------------

_PREAMBULES = (
    "Le présent document constitue les conditions générales du contrat. Il se lit "
    "conjointement avec les conditions particulières, qui prévalent sur lui en cas "
    "de divergence.",
    "Ces conditions générales fixent le cadre commun applicable à tous les contrats "
    "de cette branche. Les conditions particulières remises à l'adhérent en précisent "
    "l'étendue et les montants, et l'emportent en cas de contradiction.",
    "Le contrat est régi par le Code des assurances. Les présentes conditions générales "
    "en définissent l'économie ; les conditions particulières les adaptent au risque "
    "effectivement souscrit et priment sur elles.",
)

_DECLARATION_SINISTRE = (
    ("Tout sinistre doit être déclaré dans les cinq jours ouvrés suivant le jour où "
     "l'assuré en a eu connaissance. Ce délai est ramené à deux jours ouvrés en cas de vol "
     "et à vingt-quatre heures en cas d'incendie.", 5, 2),
    ("La déclaration doit parvenir à l'assureur au plus tard le cinquième jour ouvré "
     "après la connaissance du fait dommageable. Pour le vol, ce délai est de deux jours "
     "ouvrés ; pour l'incendie, il est d'un jour.", 5, 2),
)

_PRESCRIPTION = (
    "Toute action dérivant du contrat se prescrit par deux ans à compter de l'événement "
    "qui lui donne naissance, conformément à l'article L.114-1 du Code des assurances. "
    "Ce délai est porté à dix ans lorsque le bénéficiaire est un tiers lésé.",
    "Les actions nées du contrat sont prescrites au terme de deux années suivant "
    "l'événement générateur, en application de l'article L.114-1 du Code des assurances. "
    "Lorsqu'un tiers lésé agit, la prescription est de dix ans.",
)

_RESILIATION = (
    "Le contrat se reconduit tacitement à chaque échéance annuelle. Chacune des parties "
    "peut y mettre fin moyennant un préavis de deux mois avant l'échéance, par lettre "
    "recommandée, envoi recommandé électronique ou déclaration au siège de l'assureur.",
    "La reconduction est tacite d'année en année. La dénonciation suppose un préavis de "
    "deux mois avant la date d'échéance principale, notifié par courrier recommandé, "
    "par recommandé électronique ou par déclaration contre récépissé.",
)


def _conditions_generales() -> None:
    for produit in PRODUITS:
        for compagnie in COMPAGNIES:
            slug = f"{produit.id}-{compagnie.id}"
            garanties = "\n\n".join(
                f"**{index}.{position} — {garantie.capitalize()}.** "
                + pick(
                    (
                        "La garantie joue dans la limite du montant porté aux conditions "
                        "particulières, déduction faite de la franchise applicable.",
                        "L'engagement de l'assureur s'exerce à concurrence du plafond "
                        "fixé aux conditions particulières, sous déduction de la franchise.",
                        "Cette garantie s'applique dans les limites et sous les franchises "
                        "indiquées aux conditions particulières du contrat.",
                    )
                )
                for position, garantie in enumerate(produit.garanties, start=1)
                for index in (3,)
            )
            exclusions = "\n".join(f"- {exclusion} ;" for exclusion in produit.exclusions)
            declaration, delai_general, delai_vol = _DECLARATION_SINISTRE[
                RNG.randrange(len(_DECLARATION_SINISTRE))
            ]

            body = f"""# {produit.label} — conditions générales {compagnie.nom}

Référence CG : CG-{produit.prefixe}-{compagnie.id.upper()}-2026

## 1. Objet du contrat

{pick(_PREAMBULES)}

Le contrat a pour objet de garantir l'assuré, dans l'exercice des activités
déclarées aux conditions particulières, contre les conséquences pécuniaires des
événements définis au titre 3.

## 2. Définitions

**Assuré** — la personne morale désignée aux conditions particulières, ainsi que
ses préposés dans l'exercice de leurs fonctions.

**Sinistre** — tout événement de nature à entraîner l'application d'une garantie.
Constituent un seul et même sinistre l'ensemble des réclamations se rattachant à
une même cause technique.

**Franchise** — part du dommage qui demeure à la charge de l'assuré et qui vient
en déduction de l'indemnité. Sauf mention contraire, elle s'applique par sinistre
et non par année d'assurance.

**Plafond de garantie** — engagement maximal de l'assureur, exprimé par sinistre
et par année d'assurance aux conditions particulières.

## 3. Étendue des garanties

{garanties}

## 4. Exclusions communes

Sont exclus de toutes les garanties du contrat :

{exclusions}

Les exclusions propres à chaque garantie figurent au paragraphe correspondant.

## 5. Vie du contrat

### 5.1 Prise d'effet et durée

Le contrat prend effet à la date indiquée aux conditions particulières, sous
réserve du paiement de la première prime. Il est conclu pour une durée d'un an.

### 5.2 Reconduction et résiliation

{pick(_RESILIATION)}

L'assuré peut également résilier en cas de majoration tarifaire non justifiée par
une évolution du risque, dans les trente jours suivant la notification.

### 5.3 Déclaration des modifications du risque

Toute modification des activités, des effectifs, du chiffre d'affaires ou des
locaux doit être déclarée dans les quinze jours. À défaut, l'indemnité peut être
réduite en proportion des primes payées par rapport aux primes qui auraient été
dues.

## 6. Sinistres

### 6.1 Délai de déclaration

{declaration}

### 6.2 Obligations de l'assuré

L'assuré prend toute mesure conservatoire utile, s'abstient de toute
reconnaissance de responsabilité et transmet sans délai les actes de procédure
qu'il reçoit.

### 6.3 Expertise

L'assureur peut désigner un expert. L'assuré peut se faire assister d'un expert
de son choix, dont les honoraires sont pris en charge dans la limite prévue aux
conditions particulières. En cas de désaccord, un troisième expert est désigné.

### 6.4 Règlement

L'indemnité est réglée dans les trente jours suivant l'accord des parties sur son
montant, ou la décision judiciaire définitive.

## 7. Prescription

{pick(_PRESCRIPTION)}

## 8. Réclamations et médiation

Toute réclamation peut être adressée au service qualité de {compagnie.nom}. Une
réponse est apportée dans les dix jours ouvrables pour l'accusé de réception, et
dans les deux mois pour la réponse au fond. À défaut de solution, le Médiateur de
l'assurance peut être saisi.
"""
            add(
                Doc(
                    f"public/produits/cg-{slug}.md",
                    {
                        "type": "conditions_generales",
                        "produit": produit.id,
                        "produit_label": produit.label,
                        "compagnie": compagnie.id,
                        "compagnie_nom": compagnie.nom,
                        "reference": f"CG-{produit.prefixe}-{compagnie.id.upper()}-2026",
                        "annee": 2026,
                    },
                    body,
                )
            )

    # Questions sur les CG : formulées comme un collaborateur les poserait.
    source_dec = "public/produits/cg-decennale-norvalis.md"
    ask(
        "Un client a fait des travaux qui ne sont pas dans la liste de ses activités, "
        "est-ce que la décennale marche quand même ?",
        ["public"],
        [f"public/produits/cg-decennale-{c.id}.md" for c in COMPAGNIES],
        "exclusion « travaux réalisés en dehors des activités déclarées »",
    )
    ask(
        "On a combien de temps pour prévenir l'assureur après un cambriolage ?",
        ["public"],
        [f"public/produits/cg-{p.id}-{c.id}.md" for p in PRODUITS for c in COMPAGNIES],
        "délai de déclaration réduit pour le vol",
    )
    ask(
        "Au bout de combien de temps un client ne peut plus rien réclamer sur son contrat ?",
        ["public"],
        [f"public/produits/cg-{p.id}-{c.id}.md" for p in PRODUITS for c in COMPAGNIES],
        "prescription biennale L.114-1",
    )
    ask(
        "Quel préavis pour ne pas repartir un an de plus ?",
        ["public"],
        [f"public/produits/cg-{p.id}-{c.id}.md" for p in PRODUITS for c in COMPAGNIES],
        "préavis de résiliation à échéance",
    )
    ask(
        "Si le serveur du client n'a pas été mis à jour depuis un an, il est couvert "
        "en cas de piratage ?",
        ["public"],
        [f"public/produits/cg-cyber-{c.id}.md" for c in COMPAGNIES],
        "exclusion cyber sur les systèmes non maintenus",
    )
    ask(
        "Est-ce que l'assurance paie si on verse la rançon nous-mêmes ?",
        ["public"],
        [f"public/produits/cg-cyber-{c.id}.md" for c in COMPAGNIES],
        "exclusion rançon sans accord préalable",
    )
    ask(
        "Le client a livré un chantier et le carrelage est moche mais tient bien, "
        "c'est pris en charge ?",
        ["public"],
        [source_dec] + [f"public/produits/cg-decennale-{c.id}.md" for c in COMPAGNIES],
        "exclusion des désordres purement esthétiques",
    )
    ask(
        "Que se passe-t-il si le client a embauché du monde sans nous le dire ?",
        ["public"],
        [f"public/produits/cg-{p.id}-{c.id}.md" for p in PRODUITS for c in COMPAGNIES],
        "règle proportionnelle en cas de non-déclaration d'aggravation",
    )
    ask(
        "Un conducteur bourré au volant, ça passe en flotte ?",
        ["public"],
        [f"public/produits/cg-flotte-{c.id}.md" for c in COMPAGNIES],
        "exclusion état alcoolique",
    )
    ask(
        "Le client stocke des palettes dehors, elles sont assurées contre le vol ?",
        ["public"],
        [f"public/produits/cg-mrp-{c.id}.md" for c in COMPAGNIES],
        "exclusion marchandises stockées à l'extérieur",
    )


# --- Procédures internes du cabinet (public) ----------------------------------


def _referentiel_pieces() -> str:
    """Rend `PIECES_REFERENTIEL` en markdown ÉNUMÉRABLE.

    Le format n'est pas cosmétique. `## <clé> — <titre>` suivi d'une liste à
    puces est ce qui permet au traitement des courriels d'**énumérer** les pièces
    d'une demande, puis de demander au modèle de les cocher une par une. Un
    référentiel rédigé en prose l'obligerait à en produire la liste lui-même,
    c'est-à-dire à en inventer.

    Le document reste parfaitement lisible par un humain : c'est le même contenu,
    seulement structuré.
    """
    blocs = [
        "Ce référentiel sert à détecter ce qui manque avant d'ouvrir un dossier.\n\n"
        "Chaque section porte la clé de la demande, puis la liste exhaustive des\n"
        "pièces exigibles. Une pièce absente de cette liste ne se réclame pas.",
    ]
    for cle, titre, pieces in PIECES_REFERENTIEL:
        puces = "\n".join(f"- {piece}" for piece in pieces)
        blocs.append(f"## {cle} — {titre}\n\n{puces}")
    return "\n\n".join(blocs)


_PROCEDURES = (
    (
        "devoir-de-conseil",
        "Devoir de conseil et formalisation du besoin",
        """Le devoir de conseil n'est pas une formalité de fin de dossier : il se
construit pendant l'échange avec le client et se prouve par écrit.

## Ce qui doit être recueilli avant toute proposition

- l'activité réellement exercée, et non le seul code NAF ;
- le chiffre d'affaires du dernier exercice clos et la prévision en cours ;
- l'effectif, salariés et intérimaires compris ;
- les sinistres des cinq dernières années, y compris ceux sans suite ;
- les contrats déjà en place et leurs échéances ;
- les exigences imposées par les donneurs d'ordre ou les marchés publics.

## Ce qui doit être remis au client

Une fiche d'information formalisant le besoin exprimé, les garanties proposées,
celles qui ont été écartées et **la raison pour laquelle elles l'ont été**. C'est
ce dernier point qui est régulièrement manquant, et c'est celui qui protège le
cabinet en cas de mise en cause.

La fiche est datée, signée par le client et versée au dossier avant l'émission du
contrat. Aucun contrat n'est mis en gestion sans elle.

## Conservation

La fiche et ses annexes sont conservées pendant toute la durée du contrat, puis
cinq ans après son extinction.""",
    ),
    (
        "reclamations",
        "Traitement des réclamations clients",
        """Une réclamation est toute déclaration de mécontentement d'un client à
l'égard du cabinet. Une demande d'information, un devis ou une réclamation
d'indemnité n'en sont pas.

## Délais opposables

- accusé de réception : **dix jours ouvrables** à compter de la réception ;
- réponse sur le fond : **deux mois** au plus tard.

Lorsque la réponse ne peut être apportée dans ces deux mois, un courrier
d'attente motivé est adressé avant l'expiration du délai.

## Registre

Toute réclamation est inscrite au registre : date de réception, canal, objet,
collaborateur en charge, date d'accusé de réception, date de réponse, issue. Le
registre est revu chaque trimestre par la direction.

## Escalade

Si le client n'est pas satisfait de la réponse, il est informé par écrit de la
possibilité de saisir le Médiateur de l'assurance, avec son adresse.""",
    ),
    (
        "attestations",
        "Émission des attestations d'assurance",
        """Une attestation engage le cabinet. Elle n'est émise que sur un contrat
en vigueur et à jour de cotisation.

## Contrôles préalables obligatoires

1. le contrat est actif à la date demandée ;
2. la cotisation de la période en cours est encaissée ou fractionnée sans impayé ;
3. les activités mentionnées correspondent à celles réellement garanties ;
4. le chantier ou le marché visé entre bien dans le champ du contrat.

Un impayé bloque l'émission : la demande part au service comptabilité avant tout
autre traitement.

## Circuit

Pour les attestations standard, le cabinet émet directement depuis l'extranet de
la compagnie. Pour les attestations nominatives de chantier ou celles portant une
mention particulière, la demande passe par la compagnie et le délai dépend d'elle.

## Traçabilité

Chaque attestation émise est enregistrée dans le dossier client avec sa date, son
destinataire et son objet. Une attestation ne se réémet jamais à l'identique sans
vérifier de nouveau que le contrat est toujours en vigueur.""",
    ),
    (
        "declaration-sinistre",
        "Prise en charge d'une déclaration de sinistre",
        """Le premier réflexe est l'horodatage : la date de connaissance du fait
dommageable conditionne le respect du délai contractuel.

## À obtenir dès le premier contact

- la date et l'heure de survenance, la date de connaissance si elle diffère ;
- les circonstances décrites par le client, dans ses mots, sans interprétation ;
- l'existence d'un tiers, son identité et son assureur ;
- le dépôt de plainte pour le vol et le vandalisme ;
- les mesures conservatoires déjà prises ;
- une estimation même grossière du dommage.

## Ce qu'il ne faut jamais faire

Ne jamais indiquer au client que le sinistre est couvert ou qu'il ne l'est pas.
La position appartient à l'assureur. Le cabinet transmet, relance et explique ; il
ne qualifie pas la garantie.

## Transmission

La déclaration part à la compagnie le jour même lorsque le délai contractuel est
inférieur à quarante-huit heures, et sous deux jours ouvrés dans les autres cas.
Une copie est versée au dossier avec l'accusé de réception de la compagnie.""",
    ),
    (
        "lcb-ft",
        "Vigilance LCB-FT et connaissance du client",
        """Le cabinet est assujetti aux obligations de lutte contre le blanchiment
et le financement du terrorisme.

## Avant l'entrée en relation

Identification de la personne morale par un extrait Kbis de moins de trois mois,
et du bénéficiaire effectif dès qu'une personne physique détient plus de vingt-cinq
pour cent du capital ou des droits de vote.

## Vigilance renforcée

Elle s'applique notamment lorsque le client est une personne politiquement
exposée, lorsque le règlement provient d'un pays tiers à risque, ou lorsque le
montage juridique est inutilement complexe au regard de l'activité.

## Actualisation

Les éléments de connaissance client sont revus à chaque renouvellement, et sans
attendre en cas de changement de dirigeant ou d'actionnariat.

## Déclaration de soupçon

Le soupçon se déclare à Tracfin. Il ne se discute pas avec le client, et l'existence
de la déclaration ne lui est jamais révélée.""",
    ),
    (
        "rgpd-conservation",
        "Données personnelles et durées de conservation",
        """Le cabinet traite des données personnelles de clients, de prospects et de
tiers lésés.

## Durées appliquées

- prospects non convertis : trois ans à compter du dernier contact ;
- dossiers clients : durée du contrat, puis cinq ans après son extinction ;
- pièces de sinistre : cinq ans après clôture, dix ans en cas de dommage corporel ;
- pièces LCB-FT : cinq ans après la fin de la relation d'affaires ;
- enregistrements téléphoniques : six mois.

## Droits des personnes

Toute demande d'accès, de rectification ou d'effacement est traitée dans le mois.
Le droit à l'effacement ne s'applique pas aux pièces dont la conservation est
imposée par une obligation légale.

## Sous-traitants

Tout prestataire accédant à des données clients fait l'objet d'un contrat
comportant les clauses de l'article 28 du RGPD. La liste est tenue à jour par la
direction.""",
    ),
    (
        "resiliation-echeance",
        "Résiliation à l'échéance et préavis",
        """La date d'échéance principale d'un contrat n'est pas la date
anniversaire de sa signature : c'est celle portée aux conditions particulières.
La confusion des deux est la première cause de préavis manqué.

## Calcul du préavis

Le préavis court à rebours depuis l'échéance principale. Deux mois est la règle
usuelle en risque d'entreprise ; certains contrats prévoient trois mois, et les
contrats de flotte de plus de dix véhicules souvent davantage. Il faut lire le
contrat, pas se fier à l'habitude.

## Alerte interne

Un point de portefeuille est fait quatre mois avant chaque échéance trimestrielle,
afin qu'aucune dénonciation utile ne soit rendue impossible par le calendrier.

## Preuve d'envoi

La dénonciation part en recommandé avec accusé de réception, ou en envoi
recommandé électronique. La date qui compte est celle de l'expédition.""",
    ),
    (
        "encaissement",
        "Encaissement des primes et impayés",
        """Le cabinet encaisse pour le compte des compagnies. Les fonds transitent
par un compte séparé.

## Relance

- première relance : dix jours après l'échéance de fractionnement ;
- seconde relance : vingt jours ;
- au-delà, le dossier part à la compagnie qui engage la mise en demeure.

## Effets d'un impayé

La mise en demeure ouvre un délai de trente jours au terme duquel la garantie est
suspendue, puis le contrat peut être résilié dix jours plus tard. Un client dont la
garantie est suspendue **n'a plus d'attestation valable**, et le cabinet ne doit
en aucun cas en émettre une.

## Information du client

Le client est informé par écrit dès la seconde relance des conséquences d'un
défaut de paiement sur ses garanties.""",
    ),
    (
        "souscription-nouvelle-affaire",
        "Souscription d'une nouvelle affaire",
        """## Pièces à réunir avant toute mise en concurrence

- Kbis de moins de trois mois ;
- dernier bilan ou liasse fiscale ;
- relevé de sinistralité des cinq dernières années, délivré par le précédent assureur ;
- attestation du contrat en cours lorsqu'il en existe un ;
- descriptif des locaux, ou liste des véhicules avec immatriculations et PTAC ;
- pour le bâtiment : liste précise des activités et des techniques employées.

Le relevé de sinistralité est la pièce la plus souvent manquante, et celle qui
bloque le plus de dossiers en souscription. Elle se demande dès le premier
rendez-vous.

## Mise en concurrence

Trois compagnies au minimum sont sollicitées, sauf risque très spécifique. Les
réponses sont comparées à garanties équivalentes, pas à prime équivalente.

## Émission

Aucun contrat n'est émis sans fiche de conseil signée ni sans mandat du client.""",
    ),
    (
        "avenants",
        "Traitement des demandes d'avenant",
        """Un avenant modifie le contrat : il n'est jamais acquis tant que la
compagnie ne l'a pas émis.

## Ce qui déclenche un avenant

Changement d'adresse ou ouverture d'un établissement, ajout ou retrait d'une
activité, entrée ou sortie d'un véhicule, variation significative du chiffre
d'affaires ou de l'effectif, modification des capitaux assurés.

## Effet rétroactif

Une entrée de véhicule peut prendre effet à la date de la demande si celle-ci est
formulée sous soixante-douze heures. Au-delà, l'effet est à la date de réception
par la compagnie, et le véhicule circule sans garantie dans l'intervalle.

## Ce qu'il faut dire au client

Tant que l'avenant n'est pas émis, la modification n'est pas opposable à
l'assureur. Cette phrase doit figurer dans l'accusé de réception envoyé au client.

## Délais constatés par compagnie

Ils varient d'une semaine à trois semaines. Le délai annoncé au client est celui
de la compagnie concernée, jamais une moyenne.""",
    ),
    (
        "pieces-par-demande",
        "Pièces exigibles selon le type de demande",
        _referentiel_pieces(),
    ),
    (
        "annuaire-compagnies",
        "Interlocuteurs et délais par compagnie",
        "\n\n".join(
            [
                "Les délais ci-dessous sont ceux constatés sur les douze derniers mois, "
                "et non ceux annoncés dans les conventions.",
                *[
                    f"""## {compagnie.nom}

Service : {compagnie.gestionnaire}
Contact : {compagnie.email}

- attestation standard : **{compagnie.delai_attestation_j} jour(s) ouvré(s)** ;
- émission d'un avenant : **{compagnie.delai_avenant_j} jours ouvrés** ;
- accusé de réception d'une déclaration de sinistre : 48 heures."""
                    for compagnie in COMPAGNIES
                ],
            ]
        ),
    ),
)


def _procedures() -> None:
    for slug, titre, corps in _PROCEDURES:
        add(
            Doc(
                f"public/procedures/{slug}.md",
                {"type": "procedure_interne", "domaine": slug, "cabinet": CABINET["nom"]},
                f"# {titre}\n\n{corps}\n",
            )
        )

    ask(
        "Un client râle parce qu'on a mis trois semaines à lui répondre, "
        "on a combien de temps pour lui accuser réception ?",
        ["public"],
        ["public/procedures/reclamations.md"],
    )
    ask(
        "Qu'est-ce qu'il faut absolument garder par écrit pour se couvrir "
        "si le client nous reproche de l'avoir mal orienté ?",
        ["public"],
        ["public/procedures/devoir-de-conseil.md"],
    )
    ask(
        "On peut sortir une attestation à quelqu'un qui n'a pas payé ?",
        ["public"],
        ["public/procedures/attestations.md", "public/procedures/encaissement.md"],
    )
    ask(
        "Combien de temps garde-t-on le dossier d'un prospect qui n'a jamais signé ?",
        ["public"],
        ["public/procedures/rgpd-conservation.md"],
    )
    ask(
        "Le client me demande si son sinistre est couvert, je réponds quoi ?",
        ["public"],
        ["public/procedures/declaration-sinistre.md"],
    )
    ask(
        "Quels papiers demander quand quelqu'un veut rajouter une camionnette ?",
        ["public"],
        ["public/procedures/pieces-par-demande.md", "public/procedures/avenants.md"],
    )
    ask(
        "Il manque quoi typiquement pour monter un dossier neuf en entreprise ?",
        ["public"],
        [
            "public/procedures/souscription-nouvelle-affaire.md",
            "public/procedures/pieces-par-demande.md",
        ],
        "le relevé de sinistralité est la pièce manquante récurrente",
    )
    ask(
        "Le véhicule roule-t-il assuré tant que la compagnie n'a pas renvoyé l'avenant ?",
        ["public"],
        ["public/procedures/avenants.md"],
    )
    ask(
        "Au bout de combien de temps la garantie saute quand quelqu'un ne paie plus ?",
        ["public"],
        ["public/procedures/encaissement.md"],
    )
    ask(
        "Quelle compagnie est la plus rapide pour sortir une attestation ?",
        ["public"],
        ["public/procedures/annuaire-compagnies.md"],
    )
    ask(
        "Qui vérifie-t-on exactement quand une société entre en portefeuille ?",
        ["public"],
        ["public/procedures/lcb-ft.md"],
        "bénéficiaire effectif au-delà de 25 %",
    )
    ask(
        "Pourquoi on rate des préavis de résiliation ?",
        ["public"],
        ["public/procedures/resiliation-echeance.md"],
        "confusion entre date anniversaire et échéance principale",
    )


# --- Conditions particulières (gestion) ---------------------------------------


def _conditions_particulieres() -> None:
    for contrat in CONTRATS:
        client = CLIENTS_BY_ID[contrat.client_id]
        produit = PRODUITS_BY_ID[contrat.produit_id]
        compagnie = COMPAGNIES_BY_ID[contrat.compagnie_id]
        particularites = (
            "\n".join(f"- {item}" for item in contrat.particularites)
            if contrat.particularites
            else "- néant"
        )
        plafond = (
            "illimité pour les dommages corporels"
            if contrat.plafond_keur == 0
            else f"{euros(contrat.plafond_keur * 1000)} par sinistre"
        )
        garanties = "\n".join(
            f"| {garantie.capitalize()} | {plafond} | {euros(contrat.franchise)} |"
            for garantie in produit.garanties
        )

        body = f"""# Conditions particulières {contrat.reference}

## Parties

**Souscripteur** — {client.raison}, {client.activite}, dont le siège est à
{client.cp} {client.ville}. SIREN {client.siren}, SIRET {client.siret}, code
NAF {client.naf}. Effectif déclaré : {client.effectif} personnes. Chiffre
d'affaires déclaré : {euros(client.ca_keur * 1000)}.

**Assureur** — {compagnie.nom}, {compagnie.gestionnaire}.

**Intermédiaire** — {CABINET['nom']}, ORIAS n° {CABINET['orias']}.

## Objet

Contrat {produit.label} souscrit sous la référence **{contrat.reference}**, régi
par les conditions générales CG-{produit.prefixe}-{compagnie.id.upper()}-2026 et
par les présentes conditions particulières.

## Prise d'effet et échéance

- prise d'effet : {date_fr(contrat.date_effet)} à 0 heure ;
- échéance principale : **{contrat.echeance_jour}** de chaque année ;
- durée : un an, reconductible tacitement.

## Cotisation

- prime annuelle hors taxes : **{euros(contrat.prime_ht)}** ;
- fractionnement : {contrat.fractionnement} ;
- prime TTC estimée : {euros(round(contrat.prime_ht * 1.18))}.

## Montants de garantie et franchises

| Garantie | Plafond | Franchise |
| --- | --- | --- |
{garanties}

La franchise contractuelle s'élève à **{euros(contrat.franchise)}** et s'applique
par sinistre, quelle que soit la garantie mise en jeu, sauf mention contraire
ci-dessous.

## Dispositions particulières

{particularites}

## Activités et biens garantis

Les garanties ne s'exercent que dans le cadre de l'activité déclarée
ci-dessus. Toute activité nouvelle doit être déclarée préalablement et fait
l'objet d'un avenant.

## Déclaration de sinistre

{compagnie.gestionnaire} — {compagnie.email}. Rappeler impérativement la
référence {contrat.reference}.
"""
        add(
            Doc(
                f"gestion/contrats/{contrat.reference.lower()}-{client.id}.md",
                {
                    "type": "conditions_particulieres",
                    "reference": contrat.reference,
                    "client": client.id,
                    "client_nom": client.raison,
                    "siren": client.siren.replace(" ", ""),
                    # Coordonnées du contact : c'est ce qui fait des conditions
                    # particulières le référentiel client du cabinet. Un courriel
                    # entrant se rattache d'abord par son expéditeur, et il faut
                    # bien que « cette adresse appartient à ce client » soit écrit
                    # quelque part.
                    "client_email": client.email,
                    "client_domaine": client.domaine,
                    "produit": produit.id,
                    "produit_label": produit.label,
                    "compagnie": compagnie.id,
                    "date_effet": contrat.date_effet,
                    "prime_ht": contrat.prime_ht,
                    "franchise": contrat.franchise,
                },
                body,
            )
        )


# --- Questions portant sur les contrats ---------------------------------------

_QUESTIONS_CONTRAT = (
    ("Il reste combien à la charge de {nom} en cas de pépin sur {produit} ?", "franchise"),
    ("On paie combien par an pour {produit} chez {nom} ?", "prime"),
    ("Quand tombe le renouvellement de {produit} pour {nom} ?", "echeance"),
    ("Quel assureur porte le contrat {produit} de {nom} ?", "compagnie"),
    ("C'est quel numéro de dossier, {produit} chez {nom} ?", "reference"),
)


def _questions_contrats() -> None:
    """Une question par contrat, tournante sur cinq angles.

    L'angle tourne avec l'index pour que le jeu couvre uniformément franchise,
    prime, échéance, compagnie et référence — un tirage aléatoire laisserait des
    trous et rendrait la comparaison de deux exécutions instable.
    """
    # Le fait attendu est écrit EXACTEMENT comme le document le porte : c'est ce
    # qui permet de vérifier sa présence dans le texte rendu sans interprétation.
    # Un fait reformulé mesurerait notre capacité à paraphraser, pas la
    # couverture du fragment.
    faits = {
        "franchise": lambda c: euros(c.franchise),
        "prime": lambda c: euros(c.prime_ht),
        "echeance": lambda c: c.echeance_jour,
        "compagnie": lambda c: COMPAGNIES_BY_ID[c.compagnie_id].nom,
        "reference": lambda c: c.reference,
    }

    for index, contrat in enumerate(CONTRATS):
        client = CLIENTS_BY_ID[contrat.client_id]
        produit = PRODUITS_BY_ID[contrat.produit_id]
        gabarit, angle = _QUESTIONS_CONTRAT[index % len(_QUESTIONS_CONTRAT)]
        # Raison sociale sans la forme juridique : un collaborateur dit « Bativert »,
        # pas « SARL BATIVERT ». Le document, lui, porte la forme complète.
        nom_courant = (
            client.raison.replace("SARL ", "")
            .replace("SAS ", "")
            .replace("EURL ", "")
            .replace("SELARL ", "")
            .title()
        )
        ask(
            gabarit.format(nom=nom_courant, produit=produit.court),
            ["gestion", "public"],
            [f"gestion/contrats/{contrat.reference.lower()}-{client.id}.md"],
            f"angle : {angle}",
            fact=faits[angle](contrat),
        )


# --- Avenants (gestion) -------------------------------------------------------

_AVENANTS = (
    ("FLA-2023-0088", "2026-01-12", "ajout de véhicule",
     "Entrée du porteur immatriculé **GK-771-VB**, PTAC 19 tonnes, mis en circulation "
     "le 04/12/2025, usage transport de marchandises pour compte d'autrui.",
     "Prime annuelle portée de 48 600 € à 51 240 € HT. Effet au 12/01/2026."),
    ("FLA-2023-0088", "2026-04-02", "retrait de véhicule",
     "Sortie du véhicule léger immatriculé DP-118-KQ, cédé le 28/03/2026.",
     "Prime annuelle ramenée à 50 190 € HT, régularisation au prorata sur la "
     "prochaine quittance."),
    ("DEC-2024-0117", "2026-02-20", "extension d'activité",
     "Extension aux travaux de **ravalement avec isolation thermique par l'extérieur**, "
     "technique courante sous avis technique en cours de validité.",
     "Surprime de 1 480 € HT par an. L'extension ne couvre que les chantiers ouverts "
     "postérieurement au 20/02/2026."),
    ("MRP-2025-0342", "2026-03-05", "modification de capitaux",
     "Capitaux marchandises portés de 180 000 € à 260 000 € à la suite de "
     "l'extension du dépôt de liants.",
     "Surprime de 610 € HT par an."),
    ("CYB-2025-0456", "2026-02-14", "condition de garantie",
     "Mise en conformité de l'authentification multifacteur constatée sur l'ensemble "
     "des accès distants ; la réserve posée à la souscription est levée.",
     "Sans incidence tarifaire."),
    ("MRP-2024-0295", "2026-01-28", "ajout d'établissement",
     "Déclaration du second point de vente, 4 rue de la Bugallière à Orvault, "
     "surface commerciale 92 m², laboratoire 74 m².",
     "Prime annuelle portée de 5 310 € à 6 940 € HT."),
    ("RCP-2025-0455", "2026-05-11", "augmentation de plafond",
     "Plafond de la garantie faute professionnelle porté de 10 000 000 € à "
     "15 000 000 € par année d'assurance, à la demande d'un donneur d'ordre.",
     "Surprime de 2 300 € HT par an."),
    ("FLA-2025-0390", "2026-03-18", "ajout de véhicule",
     "Entrée du fourgon aménagé immatriculé **HB-204-ZL**, mis en circulation "
     "le 02/03/2026, conducteur principal M. Steve Marcelin.",
     "Prime annuelle portée à 10 580 € HT."),
    ("MRP-2025-0366", "2026-04-04", "extension saisonnière",
     "Confirmation de l'exploitation de la terrasse du 1er avril au 31 octobre 2026, "
     "60 couverts, mobilier rentré chaque soir.",
     "Sans incidence tarifaire, sous réserve du rentrage effectif du mobilier."),
    ("DEC-2024-0144", "2026-06-09", "extension d'activité",
     "Extension aux bornes de recharge de puissance supérieure à 22 kW, "
     "dans la limite de 50 kW en courant continu.",
     "Surprime de 940 € HT par an."),
    ("RCP-2025-0318", "2026-02-27", "modification de plafond",
     "Garantie véhicules confiés portée de 400 000 € à 550 000 € tous véhicules "
     "confondus, à la suite de l'agrandissement du parc de gardiennage.",
     "Surprime de 780 € HT par an."),
    ("DAB-2023-0061", "2026-05-20", "condition de garantie",
     "Contrôle annuel du réseau sprinklage réalisé le 12/05/2026, procès-verbal "
     "conforme reçu ; la majoration provisoire est supprimée.",
     "Prime annuelle ramenée de 19 900 € à 18 400 € HT."),
    ("FLA-2024-0277", "2026-06-15", "ajout de véhicule",
     "Entrée de la benne à ordures ménagères immatriculée **JC-338-RN**, "
     "PTAC 26 tonnes, mise en circulation le 30/05/2026.",
     "Prime annuelle portée à 40 100 € HT."),
    ("MRP-2025-0401", "2026-07-21", "renforcement de protection",
     "Installation d'une alarme reliée à un centre de télésurveillance et d'un "
     "rideau métallique sur la porte de service, à la suite du sinistre SIN-2026-0125.",
     "Franchise vol ramenée de 1 500 € à 750 € à compter du 01/08/2026."),
    ("RCP-2024-0212", "2026-03-30", "modification d'activité",
     "Déclaration de l'activité de syndic de copropriété, jusqu'alors non exercée, "
     "à hauteur de 18 % du chiffre d'affaires.",
     "Prime annuelle portée de 6 400 € à 8 950 € HT."),
    ("MRP-2025-0234", "2026-01-15", "changement d'adresse",
     "Transfert du cabinet au 7 boulevard Guist'hau, 44000 Nantes, surface 210 m².",
     "Prime inchangée."),
    ("DEC-2025-0188", "2026-05-06", "extension d'activité",
     "Extension aux **pompes à chaleur air/eau**, à l'exclusion des installations "
     "géothermiques qui restent hors garantie.",
     "Surprime de 720 € HT par an."),
    ("CYB-2026-0511", "2026-04-27", "condition de garantie",
     "À la suite du sinistre SIN-2026-0067, l'assureur exige une sauvegarde "
     "hors ligne hebdomadaire et un test de restauration trimestriel.",
     "Franchise portée de 5 000 € à 12 500 € au 01/05/2026."),
    ("FLA-2025-0320", "2026-02-09", "ajout de véhicule",
     "Entrée de la dépanneuse immatriculée **FD-662-WX**, PTAC 3,5 tonnes, "
     "usage dépannage et remorquage.",
     "Prime annuelle portée à 7 340 € HT."),
    ("RCP-2024-0278", "2026-06-30", "augmentation de plafond",
     "Garantie atteinte accidentelle à l'environnement portée de 2 000 000 € à "
     "3 500 000 € par sinistre.",
     "Surprime de 1 900 € HT par an."),
)


def _avenants() -> None:
    from entities import CONTRATS_BY_REF

    for numero, (ref, date, nature, objet, effet) in enumerate(_AVENANTS, start=1):
        contrat = CONTRATS_BY_REF[ref]
        client = CLIENTS_BY_ID[contrat.client_id]
        produit = PRODUITS_BY_ID[contrat.produit_id]
        compagnie = COMPAGNIES_BY_ID[contrat.compagnie_id]
        avenant_ref = f"AV-{date[:4]}-{numero:04d}"

        body = f"""# Avenant {avenant_ref} au contrat {ref}

**Souscripteur** — {client.raison}, SIREN {client.siren}
**Contrat** — {ref}, {produit.label}
**Assureur** — {compagnie.nom}
**Nature de la modification** — {nature}
**Date d'effet** — {date_fr(date)}

## Objet de l'avenant

{objet}

## Conséquences

{effet}

## Dispositions inchangées

Toutes les autres clauses des conditions particulières {ref} et des conditions
générales demeurent applicables sans changement.

Fait à {CABINET['ville']}, le {date_fr(date)}.
"""
        add(
            Doc(
                f"gestion/avenants/{avenant_ref.lower()}-{client.id}-{contrat.produit_id}.md",
                {
                    "type": "avenant",
                    "reference": avenant_ref,
                    "contrat": ref,
                    "client": client.id,
                    "client_nom": client.raison,
                    "produit": contrat.produit_id,
                    "compagnie": contrat.compagnie_id,
                    "date_effet": date,
                    "nature": nature,
                },
                body,
            )
        )

    ask(
        "Le camion GK-771-VB, il est bien rentré dans la flotte de Tourneix ?",
        ["gestion", "public"],
        ["gestion/avenants/av-2026-0001-tourneix-flotte.md"],
    )
    ask(
        "Est-ce que Bativert peut faire de l'isolation par l'extérieur maintenant ?",
        ["gestion", "public"],
        ["gestion/avenants/av-2026-0003-bativert-decennale.md"],
    )
    ask(
        "Pourquoi la cotisation de la boulangerie d'Orvault a augmenté ?",
        ["gestion", "public"],
        ["gestion/avenants/av-2026-0006-petrin-mrp.md"],
    )
    ask(
        "Chapuis a le droit de poser des pompes à chaleur ?",
        ["gestion", "public"],
        ["gestion/avenants/av-2026-0017-chapuis-decennale.md"],
        "extension air/eau, géothermie toujours exclue",
    )
    ask(
        "Voltis peut installer des bornes rapides au-delà de 22 kW ?",
        ["gestion", "public"],
        ["gestion/avenants/av-2026-0010-voltis-decennale.md"],
    )
    ask(
        "La franchise de Lumigraph sur le vol a bougé après le cambriolage ?",
        ["gestion", "public"],
        ["gestion/avenants/av-2026-0014-lumigraph-mrp.md"],
    )
    ask(
        "L'agence immobilière fait du syndic maintenant, c'est déclaré ?",
        ["gestion", "public"],
        ["gestion/avenants/av-2026-0015-marelle-rc_pro.md"],
    )
    ask(
        "Qu'est-ce que l'assureur a exigé de Lumigraph après le chiffrement ?",
        ["gestion", "public"],
        ["gestion/avenants/av-2026-0018-lumigraph-cyber.md"],
    )


# --- Attestations (gestion) ---------------------------------------------------

_MOTIFS_ATTESTATION = (
    "marché public de la Ville de {ville}",
    "appel d'offres d'un donneur d'ordre privé",
    "renouvellement de référencement fournisseur",
    "demande du maître d'ouvrage avant ouverture de chantier",
    "constitution d'un dossier de sous-traitance",
    "demande de la banque dans le cadre d'un financement",
)


def _attestations() -> None:
    numero = 0
    for contrat in CONTRATS:
        # Une attestation sur deux : toutes les lignes n'en génèrent pas dans la vraie
        # vie, et un corpus uniforme ne ressemble à aucun portefeuille réel.
        if RNG.random() > 0.62:
            continue
        numero += 1
        client = CLIENTS_BY_ID[contrat.client_id]
        produit = PRODUITS_BY_ID[contrat.produit_id]
        compagnie = COMPAGNIES_BY_ID[contrat.compagnie_id]
        emission = f"2026-{RNG.randrange(1, 8):02d}-{RNG.randrange(1, 28):02d}"
        motif = pick(_MOTIFS_ATTESTATION).format(ville=client.ville)
        att_ref = f"ATT-2026-{numero:04d}"

        periode = (
            f"du {date_fr(contrat.date_effet)} au "
            f"{contrat.echeance_jour} {int(contrat.date_effet[:4]) + 2}"
        )
        mention = (
            "\n\n**Mention obligatoire.** La présente attestation ne peut engager "
            "l'assureur au-delà des clauses et conditions du contrat auquel elle se "
            "réfère. Elle ne vaut que pour les activités expressément énumérées "
            "ci-dessus, et pour la période indiquée."
        )

        body = f"""# Attestation d'assurance {att_ref}

{compagnie.nom} atteste que :

**{client.raison}**, SIREN {client.siren}, dont le siège social est situé
{client.cp} {client.ville}, est titulaire du contrat **{contrat.reference}**
garantissant sa {produit.label}.

## Période de validité

Garantie en cours à la date d'émission, {periode}, sous réserve du paiement des
cotisations.

## Activités garanties

{client.activite.capitalize()}.

## Montants

- plafond de garantie : {"illimité pour les dommages corporels" if contrat.plafond_keur == 0 else euros(contrat.plafond_keur * 1000) + " par sinistre"} ;
- franchise : {euros(contrat.franchise)} par sinistre.

## Motif de la demande

{motif.capitalize()}.
{mention}

Émise le {date_fr(emission)} par {CABINET['nom']}, ORIAS n° {CABINET['orias']}.
"""
        add(
            Doc(
                f"gestion/attestations/{att_ref.lower()}-{client.id}-{contrat.produit_id}.md",
                {
                    "type": "attestation",
                    "reference": att_ref,
                    "contrat": contrat.reference,
                    "client": client.id,
                    "client_nom": client.raison,
                    "produit": contrat.produit_id,
                    "compagnie": contrat.compagnie_id,
                    "date_emission": emission,
                    "motif": motif,
                },
                body,
            )
        )


# --- Sinistres ----------------------------------------------------------------


def _sinistres() -> None:
    from entities import CONTRATS_BY_REF

    for sinistre in SINISTRES:
        contrat = CONTRATS_BY_REF[sinistre.contrat_ref]
        client = CLIENTS_BY_ID[contrat.client_id]
        produit = PRODUITS_BY_ID[contrat.produit_id]
        compagnie = COMPAGNIES_BY_ID[contrat.compagnie_id]

        reste = max(0, sinistre.montant_estime - contrat.franchise)
        body = f"""# Déclaration de sinistre {sinistre.reference}

## Identification

- assuré : **{client.raison}**, SIREN {client.siren} ;
- contrat : **{contrat.reference}** — {produit.label} ;
- assureur : {compagnie.nom} ;
- nature : **{sinistre.nature}**.

## Chronologie

- survenance : {date_fr(sinistre.date_survenance)} ;
- connaissance par l'assuré : {date_fr(sinistre.date_survenance)} ;
- déclaration au cabinet : {date_fr(sinistre.date_declaration)} ;
- transmission à la compagnie : {date_fr(sinistre.date_declaration)}.

## Circonstances déclarées

{sinistre.circonstances.capitalize()}.

Les circonstances sont reprises telles que le client les a décrites. Le cabinet
ne se prononce pas sur la mobilisation des garanties.

## Évaluation provisoire

- dommage estimé : **{euros(sinistre.montant_estime)}** ;
- franchise contractuelle : {euros(contrat.franchise)} ;
- indemnité prévisionnelle avant expertise : {euros(reste)}.

## État du dossier

**{sinistre.statut.capitalize()}.**
{f"Expert désigné : {sinistre.expert}." if sinistre.expert else "Aucun expert désigné à ce stade."}

## Suites à donner

Relance de la compagnie sous huit jours ouvrés en l'absence de position. Le
client est tenu informé à chaque étape, sans anticipation sur la décision de
l'assureur.
"""
        add(
            Doc(
                f"sinistres/declarations/{sinistre.reference.lower()}-{client.id}.md",
                {
                    "type": "declaration_sinistre",
                    "reference": sinistre.reference,
                    "contrat": contrat.reference,
                    "client": client.id,
                    "client_nom": client.raison,
                    "produit": contrat.produit_id,
                    "compagnie": contrat.compagnie_id,
                    "date_survenance": sinistre.date_survenance,
                    "nature": sinistre.nature,
                    "montant_estime": sinistre.montant_estime,
                    "statut": sinistre.statut,
                },
                body,
            )
        )

    _rapports_expertise()

    ask(
        "Où en est le dossier du dégât des eaux chez le maçon ?",
        ["sinistres", "public"],
        ["sinistres/declarations/sin-2026-0041-bativert.md"],
    )
    ask(
        "Combien nous coûte l'histoire du chiffrement chez l'imprimeur ?",
        ["sinistres", "public"],
        ["sinistres/declarations/sin-2026-0067-lumigraph.md"],
    )
    ask(
        "Le camion poubelle qui a pris feu, on sait pourquoi ?",
        ["sinistres", "public"],
        ["sinistres/declarations/sin-2026-0090-hexatri.md"],
        "chargement d'un déchet non conforme",
    )
    ask(
        "Quel expert suit la fissure de la maison livrée en 2023 ?",
        ["sinistres", "public"],
        ["sinistres/declarations/sin-2025-0388-bativert.md"],
    )
    ask(
        "La boulangerie a perdu tout son stock congelé, c'est réglé ?",
        ["sinistres", "public"],
        ["sinistres/declarations/sin-2026-0103-petrin.md"],
    )
    ask(
        "Un client de Novaflux les attaque sur une mise à jour ratée, on en est où ?",
        ["sinistres", "public"],
        ["sinistres/declarations/sin-2026-0112-novaflux.md"],
    )
    ask(
        "L'alarme était branchée le soir du cambriolage chez l'imprimeur ?",
        ["sinistres", "public"],
        ["sinistres/declarations/sin-2026-0125-lumigraph.md"],
        "l'alarme n'était pas armée — point de discussion avec l'assureur",
    )


_EXPERTISES = (
    ("SIN-2026-0041", "2026-02-04",
     "Le flexible d'alimentation présentait une rupture par vieillissement du tresse "
     "inox. Aucun défaut d'entretien caractérisé n'a été relevé : le local social a été "
     "rénové en 2022 et l'installation est conforme.",
     "Dommage retenu à 7 900 €, dont 1 200 € de recherche de fuite et 2 400 € de "
     "marchandises avariées. Garantie acquise sous déduction de la franchise."),
    ("SIN-2026-0067", "2026-03-27",
     "L'intrusion s'est faite par un accès VPN dont le second facteur n'était pas activé. "
     "Le NAS de sauvegarde était monté en permanence sur le réseau, ce qui explique son "
     "chiffrement simultané. Les sauvegardes hors ligne dataient de onze jours.",
     "Préjudice retenu à 41 300 €, dont 18 000 € de perte d'exploitation sur quatre jours. "
     "L'assureur ne retient pas l'exclusion de système non maintenu, les correctifs ayant "
     "été appliqués en janvier 2026, mais impose de nouvelles conditions de garantie."),
    ("SIN-2025-0388", "2026-01-16",
     "La fissuration traversante affecte le mur de refend porteur sud. Elle est imputable "
     "à un tassement différentiel des fondations, dont la profondeur d'ancrage est "
     "inférieure à celle prescrite par l'étude de sol.",
     "Désordre de nature décennale : l'ouvrage est impropre à sa destination. "
     "Coût de reprise chiffré à 34 700 €. Responsabilité du gros œuvre engagée."),
    ("SIN-2026-0081", "2026-05-02",
     "Le variateur de la cisaille n° 3 a subi une surtension consécutive à un défaut du "
     "réseau. La machine, mise en service en 2016, est dans la limite d'âge de la garantie "
     "bris de machine.",
     "Remise en état chiffrée à 24 900 €, immobilisation de six jours ouvrés indemnisée "
     "au titre de la perte d'exploitation à hauteur de 8 100 €."),
    ("SIN-2026-0090", "2026-06-11",
     "Le départ de feu trouve son origine dans une batterie lithium déposée dans le flux "
     "d'ordures ménagères. La benne était conforme et le contrôle technique à jour.",
     "Véhicule classé économiquement irréparable, valeur de remplacement à dire d'expert "
     "fixée à 78 500 €. Recours envisagé contre le producteur du déchet s'il est identifié."),
)


def _rapports_expertise() -> None:
    from entities import CONTRATS_BY_REF, SINISTRES_BY_REF

    for numero, (ref, date, constats, conclusions) in enumerate(_EXPERTISES, start=1):
        sinistre = SINISTRES_BY_REF[ref]
        contrat = CONTRATS_BY_REF[sinistre.contrat_ref]
        client = CLIENTS_BY_ID[contrat.client_id]
        rapport = f"EXP-2026-{numero:04d}"

        body = f"""# Rapport d'expertise {rapport}

**Sinistre** — {ref}
**Assuré** — {client.raison}
**Contrat** — {contrat.reference}
**Expert** — {sinistre.expert or "non désigné"}
**Date de la visite** — {date_fr(date)}

## Constatations

{constats}

## Conclusions

{conclusions}

## Réserves

Le présent rapport est établi sous les réserves d'usage. Il ne préjuge pas de la
position définitive de l'assureur sur la mobilisation des garanties, qui relève
de la seule appréciation de la compagnie au vu des conditions du contrat.
"""
        add(
            Doc(
                f"sinistres/expertises/{rapport.lower()}-{client.id}.md",
                {
                    "type": "rapport_expertise",
                    "reference": rapport,
                    "sinistre": ref,
                    "contrat": contrat.reference,
                    "client": client.id,
                    "client_nom": client.raison,
                    "date_visite": date,
                },
                body,
            )
        )

    ask(
        "L'expert a dit quoi sur l'origine de la fissure chez le maçon ?",
        ["sinistres", "public"],
        ["sinistres/expertises/exp-2026-0003-bativert.md"],
        "tassement différentiel, ancrage insuffisant",
    )
    ask(
        "Pourquoi les sauvegardes de l'imprimeur n'ont pas servi ?",
        ["sinistres", "public"],
        ["sinistres/expertises/exp-2026-0002-lumigraph.md"],
        "NAS monté en permanence, chiffré avec le reste",
    )
    ask(
        "Qu'est-ce qui a mis le feu à la benne ?",
        ["sinistres", "public"],
        ["sinistres/expertises/exp-2026-0005-hexatri.md"],
        "batterie lithium dans le flux d'OM",
    )


# --- Devis et propositions (production) ---------------------------------------

_PROSPECTS = (
    ("SARL MENUISERIE HALLOUIN", "784 220 315", "Vallet", "44330", "43.32A",
     "travaux de menuiserie bois et PVC", 9, 980, "decennale", "norvalis", 5400),
    ("SAS OCÉANE PAYSAGE", "891 037 462", "Guérande", "44350", "81.30Z",
     "services d'aménagement paysager", 15, 1420, "rc_pro", "artemis", 2100),
    ("SARL PRESSING DU MARCHÉ", "512 908 337", "Saint-Nazaire", "44600", "96.01B",
     "blanchisserie-teinturerie de détail", 5, 410, "mrp", "mutuas", 1650),
    ("SAS KINETIK STUDIO", "930 664 128", "Nantes", "44000", "62.01Z",
     "programmation informatique et édition de logiciels", 18, 2600, "cyber", "orlane", 6800),
    ("EURL TAXI LOIRE", "677 145 902", "Bouaye", "44830", "49.32Z",
     "transport de voyageurs par taxi", 4, 320, "flotte", "artemis", 7900),
    ("SAS CHARPENTES DU VIGNOBLE", "455 803 216", "Clisson", "44190", "43.91B",
     "travaux de charpente et couverture", 22, 3100, "decennale", "norvalis", 11200),
)


def _devis() -> None:
    for numero, (
        raison, siren, ville, cp, naf, activite, effectif, ca, produit_id, compagnie_id, prime
    ) in enumerate(_PROSPECTS, start=1):
        produit = PRODUITS_BY_ID[produit_id]
        compagnie = COMPAGNIES_BY_ID[compagnie_id]
        ref = f"DEV-2026-{numero:04d}"
        date = f"2026-0{(numero % 7) + 1}-{(numero * 3) % 28 + 1:02d}"
        manquantes = pick(
            (
                "relevé de sinistralité des cinq derniers exercices",
                "dernier bilan clos et liasse fiscale",
                "extrait Kbis de moins de trois mois",
            )
        )

        body = f"""# Proposition d'assurance {ref}

**Prospect** — {raison}, SIREN {siren}
**Activité** — {activite}, code NAF {naf}
**Siège** — {cp} {ville}
**Effectif** — {effectif} personnes — **Chiffre d'affaires** — {euros(ca * 1000)}
**Produit** — {produit.label}
**Compagnie sollicitée** — {compagnie.nom}
**Date de la proposition** — {date_fr(date)}

## Besoin exprimé par le prospect

Le prospect recherche une couverture {produit.court} adaptée à son activité, avec
une entrée en vigueur souhaitée sous trente jours. Il est actuellement assuré
ailleurs et souhaite comparer à garanties équivalentes.

## Proposition tarifaire

- prime annuelle proposée : **{euros(prime)} HT** ;
- franchise proposée : {euros(round(prime * 0.15 / 50) * 50)} par sinistre ;
- fractionnement possible : trimestriel sans majoration.

## Garanties proposées

{chr(10).join(f"- {garantie} ;" for garantie in produit.garanties)}

## Garanties écartées et motif

La garantie protection juridique étendue a été écartée : le prospect dispose déjà
d'un contrat dédié auprès d'un autre assureur, et la superposition n'apporterait
aucune indemnité supplémentaire.

## Pièces encore manquantes

- **{manquantes}**.

La proposition ne pourra être transformée en contrat tant que cette pièce n'est
pas au dossier. Elle a été réclamée par courriel le {date_fr(date)}.

## Validité

Proposition valable trente jours à compter de sa date d'émission, sous réserve de
l'accord définitif de {compagnie.nom} au vu des pièces complètes.
"""
        add(
            Doc(
                f"production/propositions/{ref.lower()}-{raison.split()[-1].lower()}.md",
                {
                    "type": "proposition",
                    "reference": ref,
                    "prospect": raison,
                    "siren": siren.replace(" ", ""),
                    "produit": produit_id,
                    "compagnie": compagnie_id,
                    "date": date,
                    "prime_ht": prime,
                    "statut": "en attente de pièces",
                },
                body,
            )
        )

    ask(
        "Il manque quoi pour transformer le devis du charpentier de Clisson ?",
        ["production", "public"],
        ["production/propositions/dev-2026-0006-vignoble.md"],
    )
    ask(
        "On a proposé combien au studio de dev pour le cyber ?",
        ["production", "public"],
        ["production/propositions/dev-2026-0004-studio.md"],
    )
    ask(
        "Pourquoi on n'a pas mis de protection juridique au paysagiste ?",
        ["production", "public"],
        ["production/propositions/dev-2026-0002-paysage.md"],
    )


# --- Direction ----------------------------------------------------------------


def _direction() -> None:
    lignes = "\n".join(
        f"| {compagnie.nom} | {taux} % | {taux_sin} % | {delai} jours |"
        for compagnie, taux, taux_sin, delai in zip(
            COMPAGNIES, (18, 14, 22, 11), (12, 9, 15, 8), (45, 60, 30, 75), strict=True
        )
    )
    add(
        Doc(
            "direction/commissions-2026.md",
            {"type": "note_direction", "domaine": "commissions", "annee": 2026},
            f"""# Taux de commission et délais de reversement — exercice 2026

Document interne. Ces taux sont ceux négociés dans les conventions de courtage en
vigueur ; ils ne sont **jamais** communiqués au client.

| Compagnie | Commission IARD | Commission sinistres gérés | Délai de reversement |
| --- | --- | --- | --- |
{lignes}

## Lecture

Mutuas Professionnels offre le meilleur taux et le reversement le plus rapide,
mais son appétit de souscription est étroit sur les activités du bâtiment.
Orlane Risques Spéciaux commissionne le moins, en contrepartie d'une capacité de
souscription que les trois autres n'ont pas sur le cyber et l'environnement.

## Règle interne

Le taux de commission **ne doit jamais entrer** dans le choix de la compagnie
proposée au client. La comparaison se fait à garanties équivalentes. Toute
proposition dont le classement diverge du classement technique doit être motivée
par écrit au dossier.
""",
        )
    )

    add(
        Doc(
            "direction/budget-cabinet-2026.md",
            {"type": "note_direction", "domaine": "budget", "annee": 2026},
            """# Budget de fonctionnement 2026

## Enveloppes votées

| Poste | Enveloppe annuelle |
| --- | --- |
| Masse salariale | 812 000 € |
| Logiciel de courtage et licences | 46 500 € |
| Outillage numérique et IA | 28 000 € |
| Formation réglementaire (DDA, 15 h/an) | 11 200 € |
| Locaux et charges | 63 000 € |
| Assurance RC Pro du cabinet | 9 400 € |

## Règles d'engagement

Toute dépense supérieure à **4 000 € HT** requiert une double validation, celle de
la direction et celle de la gérance. Les engagements sont gelés à compter du
**15 décembre** et rouverts le 5 janvier.

## Outillage numérique

L'enveloppe de 28 000 € couvre l'expérimentation d'un assistant de traitement des
demandes clients. Le critère de poursuite n'est pas le nombre de fonctionnalités,
mais la réduction mesurée du délai entre la réception d'un courriel et le
traitement effectif du dossier.
""",
        )
    )

    add(
        Doc(
            "direction/convention-artemis.md",
            {"type": "convention", "compagnie": "artemis", "annee": 2026},
            """# Convention de courtage — Artémis Assurances

## Délégation de souscription

Le cabinet dispose d'une délégation de souscription pour les risques suivants :

- multirisque professionnelle jusqu'à 3 000 000 € de capitaux ;
- RC professionnelle jusqu'à 5 000 000 € de plafond ;
- flotte automobile jusqu'à 15 véhicules.

Au-delà, l'accord préalable du souscripteur d'Artémis est requis.

## Délégation de gestion des sinistres

Le cabinet règle directement les sinistres matériels inférieurs à 3 000 €, sur
ses fonds, avec remboursement mensuel sur état. Au-delà, le dossier est instruit
par la compagnie.

## Encaissement

Le cabinet encaisse les primes et reverse sous 45 jours à terme échu.

## Résiliation de la convention

Préavis de six mois. En cas de résiliation, le portefeuille reste la propriété du
cabinet, qui dispose de douze mois pour le replacer.
""",
        )
    )

    ask(
        "On touche combien de commission chez Mutuas ?",
        ["direction"],
        ["direction/commissions-2026.md"],
    )
    ask(
        "À partir de quel montant il faut deux signatures pour engager une dépense ?",
        ["direction"],
        ["direction/budget-cabinet-2026.md"],
    )
    ask(
        "Jusqu'à combien de véhicules on peut souscrire tout seul chez Artémis ?",
        ["direction"],
        ["direction/convention-artemis.md"],
    )
    ask(
        "On peut régler un petit sinistre sans passer par la compagnie ?",
        ["direction"],
        ["direction/convention-artemis.md"],
    )


# --- Fils de courriels (gestion) ----------------------------------------------

_THREADS = (
    ("bativert", "MRP-2025-0342", "Dégât des eaux dans le local social", "claim", (
        ("client", "Bonjour Aurélie,\n\nOn a eu une mauvaise surprise en arrivant lundi : "
         "il y avait de l'eau partout dans le local social et ça a coulé jusque dans le "
         "bureau à côté. Apparemment c'est le tuyau sous l'évier qui a lâché pendant le "
         "week-end.\n\nLes sacs de liant qui étaient stockés au fond sont fichus.\n\n"
         "Qu'est-ce qu'on doit faire ?"),
        ("cabinet", "Bonjour Sandrine,\n\nJe suis désolée pour ce sinistre. Je déclare "
         "aujourd'hui à Norvalis sous votre contrat MRP-2025-0342.\n\nPour avancer il me "
         "faudrait :\n- le constat amiable dégât des eaux si un tiers est concerné ;\n"
         "- la facture du plombier pour la recherche de fuite ;\n- des photos des zones "
         "touchées ;\n- une estimation chiffrée des sacs de liant perdus.\n\n"
         "Surtout ne jetez rien avant le passage de l'expert."),
        ("client", "Le plombier est passé mardi, je vous joins sa facture. Pour les sacs "
         "j'ai compté 3 400 € de marchandise. Les photos arrivent dans un autre mail, "
         "elles sont trop lourdes.\n\nL'expert passe quand ?"),
    )),
    ("tourneix", "FLA-2023-0088", "Nouveau porteur à assurer", "contract_amendment", (
        ("client", "Bonjour,\n\nOn vient de prendre livraison d'un nouveau porteur, "
         "il faut le mettre sur le contrat flotte. Il doit rouler dès lundi.\n\n"
         "Immat GK-771-VB, 19 tonnes.\n\nMerci de faire au plus vite."),
        ("cabinet", "Bonjour Monsieur Tourneix,\n\nJ'ai bien noté. Pour que la garantie "
         "démarre à la date de votre demande, je dois transmettre sous 72 heures — c'est "
         "faisable.\n\nIl me manque la carte grise et la date de première mise en "
         "circulation. Sans la carte grise Artémis ne prend pas la demande.\n\n"
         "Attention : tant que l'avenant n'est pas émis, la modification n'est pas "
         "opposable à l'assureur."),
        ("client", "Voici la carte grise. Mise en circulation le 04/12/2025."),
        ("cabinet", "Parfait, c'est parti chez Artémis. L'avenant AV-2026-0001 prend "
         "effet au 12/01/2026. La prime passe de 48 600 € à 51 240 € HT."),
    )),
    ("lumigraph", "CYB-2026-0511", "URGENT tout est bloqué", "claim", (
        ("client", "Aurélie,\n\nOn est complètement à l'arrêt. Ce matin plus rien ne "
         "démarre, il y a un message qui demande de payer en bitcoin. Le serveur ET le "
         "NAS de sauvegarde sont touchés.\n\nOn a des commandes à sortir jeudi, "
         "je fais quoi ??"),
        ("cabinet", "Fabrice, je prends en charge immédiatement.\n\n1. Débranchez le "
         "réseau, n'éteignez rien, ne payez surtout rien.\n2. J'active la cellule de "
         "crise Orlane, ils vous rappellent sous 2 heures.\n3. Ne touchez pas aux "
         "machines, les forensics en ont besoin.\n\nJe déclare le sinistre maintenant "
         "sous CYB-2026-0511. Franchise 5 000 €, la perte d'exploitation est couverte."),
        ("client", "Ok. Le prestataire dit que la dernière sauvegarde hors ligne date "
         "du 28 février. On est le 11 mars."),
        ("cabinet", "C'est un point que l'expert va regarder de près. Je le remonte à "
         "Orlane dès maintenant plutôt qu'ils le découvrent. Ne prenez aucun engagement "
         "sur la reprise avant leur position."),
    )),
    ("petrin", "MRP-2024-0295", "Attestation pour la mairie", "certificate_request", (
        ("client", "Bonjour,\n\nLa mairie d'Orvault me réclame une attestation "
         "d'assurance pour le marché de fourniture de pain aux écoles. Il me la faut "
         "avant vendredi.\n\nMerci !"),
        ("cabinet", "Bonjour Yannis,\n\nJe m'en occupe. Artémis sort les attestations "
         "standard sous 2 jours ouvrés, on est dans les temps.\n\nUne question : "
         "l'attestation doit-elle mentionner les deux points de vente ou uniquement "
         "celui du centre ? Le marché porte sur quel établissement ?"),
        ("client", "Les deux, c'est le laboratoire de la Bugallière qui produit."),
    )),
    ("novaflux", "RCP-2025-0455", "Mise en cause par un client", "claim", (
        ("client", "Bonjour,\n\nUn de nos clients nous met en cause : il dit qu'une "
         "montée de version qu'on a livrée en mai a cassé sa facturation et il chiffre "
         "son préjudice à 58 000 €.\n\nOn a reçu son courrier recommandé hier. "
         "Je vous le scanne.\n\nOn répond quoi ?"),
        ("cabinet", "Bonjour Inès,\n\nSurtout : ne répondez rien sur le fond et ne "
         "reconnaissez aucune responsabilité, même par téléphone. C'est une obligation "
         "contractuelle et ça pourrait vous priver de garantie.\n\nJe déclare aujourd'hui "
         "à Orlane sous RCP-2025-0455. Franchise 2 500 €, plafond 15 000 000 € depuis "
         "l'avenant de mai.\n\nIl me faut : le contrat qui vous lie à ce client, "
         "le chiffrage détaillé qu'il produit, et tous les échanges antérieurs."),
    )),
    ("sablier", "MRP-2025-0366", "Vitrine cassée cette nuit", "claim", (
        ("client", "Bonsoir,\n\nOn a retrouvé la vitrine côté quai en morceaux ce matin, "
         "quelqu'un a balancé quelque chose dedans. J'ai porté plainte au commissariat "
         "de La Baule.\n\nLe vitrier annonce 3 200 €."),
        ("cabinet", "Bonjour Nadia,\n\nDéclaration faite chez Mutuas. Le bris de glace "
         "est bien garanti sur votre MRP, franchise 700 €.\n\nEnvoyez-moi le récépissé "
         "de plainte et le devis du vitrier, et vous pouvez faire réaliser les travaux "
         "sans attendre — c'est une mesure conservatoire."),
        ("client", "Voilà les deux pièces. Merci pour la réactivité."),
        ("cabinet", "Dossier SIN-2026-0074 réglé, l'indemnité de 2 500 € part cette "
         "semaine sur votre compte."),
    )),
    ("marelle", "RCP-2024-0212", "On lance une activité de syndic", "contract_amendment", (
        ("client", "Bonjour Thomas,\n\nOn démarre une activité de syndic de copropriété "
         "à partir d'avril. Ça devrait représenter à peu près 18 % de notre chiffre.\n\n"
         "Il faut faire quelque chose côté assurance ?"),
        ("cabinet", "Bonjour Claire,\n\nOui, impérativement. Le syndic n'est pas dans "
         "vos activités déclarées : en l'état, un sinistre sur cette activité ne serait "
         "pas garanti.\n\nJe monte l'avenant chez Artémis. Il me faut le procès-verbal "
         "d'assemblée ou la décision actant le lancement, et une estimation du nombre de "
         "lots gérés la première année.\n\nAttention, ça va impacter la prime."),
        ("client", "De combien à peu près ?"),
        ("cabinet", "Artémis annonce 8 950 € au lieu de 6 400 € HT. L'avenant "
         "AV-2026-0015 prend effet au 30/03/2026."),
    )),
    ("garageduc", "RCP-2025-0318", "Question sur les véhicules confiés", "coverage_question", (
        ("client", "Bonjour,\n\nOn agrandit le parking de gardiennage. Aujourd'hui on "
         "est couvert jusqu'à combien pour les véhicules des clients qui dorment chez "
         "nous ?\n\nEt ça monte comment si besoin ?"),
        ("cabinet", "Bonjour Olivier,\n\nVotre RC Pro couvre les véhicules confiés à "
         "hauteur de 400 000 € tous véhicules confondus.\n\nSi le parc grandit, on peut "
         "porter ça à 550 000 € pour une surprime de 780 € HT par an. Dites-moi si je "
         "lance l'avenant chez Norvalis."),
        ("client", "Oui allez-y."),
    )),
    ("hexatri", "FLA-2024-0277", "Benne en feu à Trignac", "claim", (
        ("client", "Bonjour,\n\nUne de nos bennes a pris feu ce matin pendant la "
         "tournée, immat GH-905-TR. Les pompiers sont intervenus, personne n'est blessé.\n\n"
         "Le chauffeur pense qu'il y avait quelque chose d'anormal dans le chargement."),
        ("cabinet", "Bonjour Patrice,\n\nJe déclare immédiatement à Orlane. Le délai "
         "incendie est de 24 h, on est dans les temps.\n\nIl me faut le rapport "
         "d'intervention des pompiers, les photos, la carte grise et le dernier contrôle "
         "technique du véhicule.\n\nSi le producteur du déchet est identifiable, "
         "conservez tout élément : un recours est possible."),
        ("client", "Le rapport des pompiers arrive sous 8 jours d'après eux."),
    )),
    ("chapuis", "DEC-2025-0188", "Pompes à chaleur", "coverage_question", (
        ("client", "Bonjour,\n\nOn me demande de plus en plus de poser des pompes à "
         "chaleur. Est-ce que je suis couvert avec ma décennale actuelle ?"),
        ("cabinet", "Bonjour Éric,\n\nEn l'état non : vos activités déclarées couvrent "
         "la plomberie sanitaire et le chauffage à eau chaude, mais pas les PAC.\n\n"
         "Je peux étendre chez Norvalis. Il me faut votre attestation de qualification "
         "ou un justificatif d'expérience, et une estimation du chiffre d'affaires "
         "prévisionnel sur cette activité.\n\nÀ noter : la géothermie restera exclue."),
        ("client", "C'est de l'air/eau uniquement. Je vous envoie la qualif."),
        ("cabinet", "Extension actée, avenant AV-2026-0017 au 06/05/2026, "
         "surprime 720 € HT/an. Attention : elle ne vaut que pour les chantiers "
         "ouverts après cette date."),
    )),
    ("voltis", "FLA-2025-0390", "Fourgon supplémentaire", "contract_amendment", (
        ("client", "Salut,\n\nJ'ai récupéré un fourgon aménagé de plus, HB-204-ZL, "
         "mis en circulation le 2 mars. À rajouter sur la flotte.\n\nSteve"),
        ("cabinet", "Bonjour Steve,\n\nC'est noté. Carte grise reçue, avenant "
         "AV-2026-0010 transmis à Artémis, effet au 18/03/2026. La flotte passe à "
         "10 580 € HT par an."),
    )),
    ("ergomed", "MRP-2025-0234", "Déménagement du cabinet", "contract_amendment", (
        ("client", "Bonjour,\n\nNous déménageons au 7 boulevard Guist'hau à Nantes, "
         "210 m². Le déménagement est effectif au 15 janvier.\n\nQue faut-il faire ?"),
        ("cabinet", "Bonjour Docteur Vasseur,\n\nIl faut déclarer la nouvelle adresse : "
         "la garantie ne suit pas automatiquement des locaux non déclarés.\n\n"
         "Je monte l'avenant chez Mutuas. Pouvez-vous me confirmer la surface, la nature "
         "des locaux et l'existence d'une alarme ?"),
        ("client", "210 m², bureaux uniquement, alarme oui mais pas reliée."),
    )),
    ("calliope", None, "Devis pour du distanciel", "new_quote", (
        ("client", "Bonjour,\n\nOn développe la formation à distance et un client "
         "grand compte nous demande une attestation RC Pro qui mentionne explicitement "
         "le distanciel.\n\nNotre contrat le couvre ?"),
        ("cabinet", "Bonjour Léa,\n\nOui, votre RCP-2026-0602 couvre bien le présentiel "
         "et le distanciel — c'est écrit dans les dispositions particulières. En revanche "
         "l'activité d'organisme certificateur reste exclue.\n\nJe demande à Mutuas une "
         "attestation avec la mention explicite. Délai annoncé : 1 jour ouvré."),
    )),
    ("fermetal", "DAB-2023-0061", "Contrôle sprinklage", "administrative", (
        ("cabinet", "Bonjour Monsieur Lecoutre,\n\nRappel : le contrôle annuel de votre "
         "réseau sprinklage conditionne le maintien du tarif. Le procès-verbal 2025 nous "
         "est parvenu le 12 mai.\n\nSans PV conforme, Norvalis applique une majoration "
         "provisoire de la prime."),
        ("client", "Le contrôle est fait, PV conforme joint."),
        ("cabinet", "Parfait, la majoration est supprimée. La prime revient de 19 900 € "
         "à 18 400 € HT (avenant AV-2026-0012)."),
    )),
    ("petrin", "MRP-2024-0295", "Chambre froide en panne", "claim", (
        ("client", "Bonjour,\n\nCatastrophe : la chambre froide négative de la "
         "Bugallière s'est arrêtée sans qu'on s'en rende compte. Tout est perdu, "
         "on a jeté.\n\nJ'ai des photos et les bons de livraison."),
        ("cabinet", "Bonjour Yannis,\n\nLes denrées sont couvertes jusqu'à 18 000 € sur "
         "votre contrat. Franchise 600 €.\n\nEnvoyez les photos, les bons de livraison "
         "et le rapport du frigoriste sur la cause de l'arrêt — c'est la pièce que "
         "l'assureur regardera en premier."),
        ("client", "Le frigoriste dit que c'est le disjoncteur qui a sauté et que "
         "l'alarme de température n'était pas branchée."),
        ("cabinet", "Je le transmets tel quel. Dossier SIN-2026-0103."),
    )),
)


def _emails() -> None:
    from entities import CONTRATS_BY_REF

    for numero, (client_id, contrat_ref, sujet, intention, messages) in enumerate(
        _THREADS, start=1
    ):
        client = CLIENTS_BY_ID[client_id]
        collaborateur = COLLABORATEURS[numero % len(COLLABORATEURS)]
        thread_id = f"THR-2026-{numero:04d}"
        base_jour = (numero * 2) % 26 + 1
        mois = (numero % 7) + 1

        corps = []
        for position, (auteur, texte) in enumerate(messages):
            jour = min(28, base_jour + position)
            date = f"2026-{mois:02d}-{jour:02d}"
            if auteur == "client":
                expediteur = f"{client.contact} <{client.email}>"
                destinataire = f"gestion@{CABINET['domaine']}"
            else:
                expediteur = (
                    f"{collaborateur.nom} <{collaborateur.email}@{CABINET['domaine']}>"
                )
                destinataire = f"{client.contact} <{client.email}>"
            prefixe = "" if position == 0 else "Re : "
            corps.append(
                f"""## Message {position + 1} — {date_fr(date)}

**De** : {expediteur}
**À** : {destinataire}
**Objet** : {prefixe}{sujet}

{texte}"""
            )

        contrat_ligne = ""
        if contrat_ref:
            contrat = CONTRATS_BY_REF[contrat_ref]
            produit = PRODUITS_BY_ID[contrat.produit_id]
            contrat_ligne = f"\n**Contrat rattaché** — {contrat_ref} ({produit.label})"

        body = f"""# Fil de discussion {thread_id} — {sujet}

**Client** — {client.raison} (SIREN {client.siren}){contrat_ligne}
**Collaborateur en charge** — {collaborateur.nom}, {collaborateur.role}
**Qualification** — {intention}

{chr(10).join(chr(10).join(["", part]) for part in corps)}
"""
        add(
            Doc(
                f"gestion/emails/{thread_id.lower()}-{client.id}.md",
                {
                    "type": "fil_email",
                    "reference": thread_id,
                    "client": client.id,
                    "client_nom": client.raison,
                    "contrat": contrat_ref,
                    "intention": intention,
                    "sujet": sujet,
                    "collaborateur": collaborateur.nom,
                    "messages": len(messages),
                },
                body,
            )
        )

    ask(
        "Qu'est-ce qu'on a demandé au maçon après son dégât des eaux ?",
        ["gestion", "public"],
        ["gestion/emails/thr-2026-0001-bativert.md"],
    )
    ask(
        "Qu'est-ce qu'on a dit à l'imprimeur de faire tout de suite quand il s'est "
        "fait chiffrer ?",
        ["gestion", "public"],
        ["gestion/emails/thr-2026-0003-lumigraph.md"],
        "débrancher, ne rien éteindre, ne rien payer",
    )
    ask(
        "Le boulanger voulait son attestation pour quoi déjà ?",
        ["gestion", "public"],
        ["gestion/emails/thr-2026-0004-petrin.md"],
    )
    ask(
        "Qu'est-ce qu'on a répondu à Novaflux quand leur client les a mis en cause ?",
        ["gestion", "public"],
        ["gestion/emails/thr-2026-0005-novaflux.md"],
        "ne rien reconnaître, même oralement",
    )
    ask(
        "Le restaurant de La Baule a été indemnisé de combien pour sa vitrine ?",
        ["gestion", "public"],
        ["gestion/emails/thr-2026-0006-sablier.md"],
    )
    ask(
        "L'alarme de température de la chambre froide était branchée ?",
        ["gestion", "public"],
        ["gestion/emails/thr-2026-0015-petrin.md"],
    )
    ask(
        "Il manquait quoi pour rajouter le camion de Tourneix ?",
        ["gestion", "public"],
        ["gestion/emails/thr-2026-0002-tourneix.md"],
    )
    ask(
        "Le garage voulait monter sa couverture véhicules confiés, on a dit oui ?",
        ["gestion", "public"],
        ["gestion/emails/thr-2026-0008-garageduc.md"],
    )


# --- Négatifs difficiles ------------------------------------------------------


def _negatifs() -> None:
    """Questions dont la bonne réponse est de ne rien affirmer.

    Deux familles, et la seconde est la plus instructive : le client cité existe,
    le produit cité existe, mais **ce client n'a pas ce produit**. Une recherche
    sans seuil rendra alors le contrat du même produit chez un AUTRE client, avec
    un score de similarité élevé et aucune alerte. En courtage, répondre « la
    franchise cyber de Bativert est de 10 000 € » alors que Bativert n'a pas de
    contrat cyber, c'est la panne qui coûte le client.
    """

    # Famille 1 — le sujet est absent du corpus.
    absents = (
        ("Quel est le tarif de la garantie perte de licence pour les pilotes de ligne ?",
         ["public", "gestion", "production", "sinistres"],
         "produit inexistant au portefeuille"),
        ("Quelles sont nos conditions en assurance-vie et capitalisation ?",
         ["public", "gestion", "direction"],
         "le cabinet ne fait que de l'IARD professionnel"),
        ("Combien coûte la mutuelle santé collective chez Artémis ?",
         ["public", "gestion", "direction"],
         "branche santé absente du corpus"),
        ("Quel est le barème d'indemnisation des accidents du travail de nos clients ?",
         ["public", "sinistres"],
         "relève de la sécurité sociale, pas du portefeuille"),
        ("Quelle est la procédure pour assurer un drone professionnel ?",
         ["public", "production"],
         "aucune procédure ni produit drone"),
        ("Quel est le montant de la garantie financière de La Marelle ?",
         ["gestion", "public"],
         "explicitement souscrite ailleurs, montant non documenté"),
        ("Quels sont les résultats du cabinet au premier trimestre 2026 ?",
         ["direction"],
         "aucun document de résultat dans le corpus"),
        ("Quelle est la politique de télétravail du cabinet ?",
         ["public", "gestion"],
         "sujet RH absent — c'est le corpus de l'ancien POC"),
    )
    for question, groups, note in absents:
        ask_abstain(question, groups, note)

    # Famille 2 — le client n'a pas ce produit. Piège de similarité.
    #
    # Les couples sont énumérés depuis le portefeuille plutôt qu'écrits à la main :
    # une liste manuelle se désynchronise dès qu'on ajoute un contrat, et un
    # « négatif » qui n'en est plus un fait échouer la mesure dans le mauvais sens
    # — le système est puni pour avoir eu raison. L'assertion ci-dessous rend cette
    # dérive impossible.
    _GABARITS_MANQUANT = (
        "Quelle est la franchise {produit} de {nom} ?",
        "Quel est le plafond {produit} souscrit pour {nom} ?",
        "Combien coûte le contrat {produit} de {nom} ?",
        "Quelle compagnie porte la {produit} de {nom} ?",
        "Quand tombe l'échéance {produit} de {nom} ?",
    )
    couples = [
        (client, produit)
        for client in CLIENTS
        for produit in PRODUITS
        if produit.id not in {c.produit_id for c in contrats_du_client(client.id)}
    ]
    # Tri explicite puis échantillonnage sur graine : `couples` vient déjà d'un
    # ordre déterministe, mais le rendre explicite protège d'un refactor qui
    # changerait l'ordre d'itération et donc le jeu mesuré.
    couples.sort(key=lambda paire: (paire[0].id, paire[1].id))
    for index, (client, produit) in enumerate(RNG.sample(couples, 20)):
        detenus = {contrat.produit_id for contrat in contrats_du_client(client.id)}
        assert produit.id not in detenus, (
            f"{client.raison} détient un contrat {produit.id} : ce négatif n'en est pas un"
        )
        nom_courant = (
            client.raison.replace("SARL ", "")
            .replace("SAS ", "")
            .replace("EURL ", "")
            .replace("SELARL ", "")
            .title()
        )
        ask_abstain(
            _GABARITS_MANQUANT[index % len(_GABARITS_MANQUANT)].format(
                produit=produit.court, nom=nom_courant
            ),
            ["gestion", "public"],
            f"{client.raison} n'a aucun contrat {produit.label} — "
            "le corpus contient ce produit pour d'autres clients",
        )

    # Famille 3 — l'entité elle-même n'existe pas. Un identifiant plausible mais
    # inconnu doit rester sans réponse : c'est le cas où la recherche lexicale
    # rate et où la recherche dense « rapproche » de la référence la plus proche.
    fantomes = (
        ("Quelle est la franchise du contrat MRP-2025-0999 ?",
         "référence de contrat inexistante"),
        ("Où en est le sinistre SIN-2026-0400 ?",
         "référence de sinistre inexistante"),
        ("Quels contrats a la SARL BOULANGERIE MERCIER ?",
         "client inexistant au portefeuille"),
        ("Quelle est l'échéance du contrat de la SAS TRANSPORTS LEGENDRE ?",
         "client inexistant au portefeuille"),
        ("Quel avenant a été passé sur le contrat DEC-2019-0001 ?",
         "aucun contrat antérieur à 2023 dans le corpus"),
        ("Quel était le tarif de la flotte Tourneix en 2019 ?",
         "le contrat existe mais aucune donnée tarifaire avant 2023"),
        ("Combien de sinistres a déclaré Calliope Formation ?",
         "aucun sinistre pour ce client — l'absence est une réponse"),
    )
    for question, note in fantomes:
        ask_abstain(question, ["gestion", "sinistres", "public"], note)


# --- Écriture -----------------------------------------------------------------


def _front_matter(meta: dict) -> str:
    """Front-matter YAML minimal, écrit à la main.

    Pas de dépendance à PyYAML : ce script doit tourner sur une machine nue, sans
    environnement virtuel ni installation. Les valeurs restent des scalaires
    simples, ce qui rend l'échappement trivial.
    """
    lignes = ["---"]
    for cle, valeur in meta.items():
        if valeur is None:
            continue
        if isinstance(valeur, bool):
            rendu = "true" if valeur else "false"
        elif isinstance(valeur, int | float):
            rendu = str(valeur)
        else:
            texte = str(valeur).replace('"', "'")
            rendu = f'"{texte}"'
        lignes.append(f"{cle}: {rendu}")
    lignes.append("---")
    return "\n".join(lignes)


def _ecrire_corpus(racine: Path) -> None:
    if racine.exists():
        shutil.rmtree(racine)
    for doc in DOCS:
        chemin = racine / doc.path
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text(f"{_front_matter(doc.meta)}\n\n{doc.body.strip()}\n", encoding="utf-8")


_ENTETE_EVAL = """# Jeu d'évaluation du RAG — courtage IARD professionnel.
#
# GÉNÉRÉ par corpus-gen/generate.py — ne pas éditer à la main, la prochaine
# génération écraserait les modifications. Pour ajouter un cas, l'écrire dans le
# générateur : il restera aligné sur le corpus.
#
# Deux familles de cas :
#
#   expect: [...]   le ou les documents qui DOIVENT remonter dans le top-k.
#   abstain: true   aucune source ne répond — le système doit le dire, et non
#                   servir le document le plus proche. Un cas sur quatre, et
#                   c'est la part que la plupart des évaluations oublient.
#
# Les questions sont écrites dans le vocabulaire d'un collaborateur, PAS dans
# celui des documents. Un jeu rédigé en recopiant les termes des documents
# mesure la capacité à retrouver des mots, pas à répondre à une question.
"""


def _ecrire_eval(chemin: Path) -> None:
    lignes = [_ENTETE_EVAL]
    positifs = [case for case in CASES if not case.abstain]
    negatifs = [case for case in CASES if case.abstain]

    def rendu(case: Case) -> str:
        bloc = [f"- question: {_yaml_scalaire(case.question)}"]
        bloc.append(f"  groups: [{', '.join(case.groups)}]")
        if case.expect:
            bloc.append("  expect:")
            bloc.extend(f"    - {source}" for source in case.expect)
        else:
            bloc.append("  expect: []")
        if case.abstain:
            bloc.append("  abstain: true")
        if case.fact:
            bloc.append(f"  fact: {_yaml_scalaire(case.fact)}")
        if case.note:
            bloc.append(f"  note: {_yaml_scalaire(case.note)}")
        return "\n".join(bloc)

    lignes.append(f"\n# --- {len(positifs)} cas positifs " + "-" * 50 + "\n")
    lignes.extend(rendu(case) for case in positifs)
    lignes.append(f"\n# --- {len(negatifs)} négatifs difficiles " + "-" * 44 + "\n")
    lignes.extend(rendu(case) for case in negatifs)

    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text("\n".join(lignes) + "\n", encoding="utf-8")


def _yaml_scalaire(texte: str) -> str:
    """Chaîne YAML sûre sans dépendance : guillemets doubles, échappement minimal."""
    return '"' + texte.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _verifier() -> None:
    """Garde-fous : un jeu d'évaluation qui pointe à côté ne mesure rien.

    Une source attendue mais absente du corpus rendrait le cas impossible à
    satisfaire, et le rappel plafonnerait sans que personne ne comprenne pourquoi.
    """
    connus = {doc.path for doc in DOCS}
    manquants = {
        source
        for case in CASES
        for source in case.expect
        if source not in connus
    }
    if manquants:
        raise SystemExit(
            "Le jeu d'évaluation attend des documents que le générateur ne produit pas :\n  "
            + "\n  ".join(sorted(manquants))
        )

    rendus = {doc.path: doc.body for doc in DOCS}
    introuvables = [
        (case.question, case.fact)
        for case in CASES
        if case.fact
        and not any(case.fact in rendus.get(source, "") for source in case.expect)
    ]
    if introuvables:
        details = "\n  ".join(f"« {q} » attend « {f} »" for q, f in introuvables)
        raise SystemExit(
            "Des faits attendus ne figurent dans aucun document attendu — ils "
            f"seraient introuvables :\n  {details}"
        )

    doublons = [doc.path for doc in DOCS if [d.path for d in DOCS].count(doc.path) > 1]
    if doublons:
        raise SystemExit(f"Chemins produits deux fois : {sorted(set(doublons))}")

    racines = {doc.path.split("/")[0] for doc in DOCS}
    inconnues = racines - set(GROUPES)
    if inconnues:
        raise SystemExit(f"Groupes ACL inattendus (premier segment) : {sorted(inconnues)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="corpus", help="racine du corpus à écrire")
    parser.add_argument("--eval", default="eval/questions.yaml", help="jeu d'évaluation")
    parser.add_argument(
        "--dry-run", action="store_true", help="compte les documents sans rien écrire"
    )
    args = parser.parse_args()

    _conditions_generales()
    _procedures()
    _conditions_particulieres()
    _questions_contrats()
    _avenants()
    _attestations()
    _sinistres()
    _devis()
    _direction()
    _emails()
    _negatifs()
    _verifier()

    par_groupe: dict[str, int] = {}
    for doc in DOCS:
        groupe = doc.path.split("/")[0]
        par_groupe[groupe] = par_groupe.get(groupe, 0) + 1

    caracteres = sum(len(doc.body) for doc in DOCS)
    positifs = sum(1 for case in CASES if not case.abstain)
    negatifs = len(CASES) - positifs

    print(f"{len(DOCS)} documents, ~{caracteres // 1000} k caractères")
    for groupe in GROUPES:
        if groupe in par_groupe:
            print(f"  {groupe:<12} {par_groupe[groupe]:>4}")
    print(f"{len(CASES)} cas d'évaluation — {positifs} positifs, {negatifs} négatifs difficiles")

    if args.dry_run:
        print("(simulation : rien n'a été écrit)")
        return

    _ecrire_corpus(Path(args.out))
    _ecrire_eval(Path(args.eval))
    print(f"→ corpus écrit dans {args.out}/")
    print(f"→ jeu d'évaluation écrit dans {args.eval}")


if __name__ == "__main__":
    main()
