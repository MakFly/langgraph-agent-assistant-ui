"""Génère une boîte de réception `.eml` — l'entrée du traitement « courriel → dossier ».

    python3 corpus-gen/mailbox.py --out mailbox

**Pourquoi ces courriels ne sont PAS dans le corpus indexé.** Le corpus, c'est
l'historique : contrats, avenants, sinistres, échanges passés. La boîte, c'est ce
qui vient d'arriver et que personne n'a encore traité. Les mélanger ferait
disparaître le problème qu'on cherche à résoudre — un courriel déjà indexé se
rattache trivialement à lui-même.

**La difficulté de rattachement est graduée exprès**, parce que c'est là que se
joue la valeur du produit :

- `reference` — la référence du contrat est écrite dans le corps. Cas facile,
  et minoritaire dans la vraie vie.
- `domaine` — rien qu'une adresse d'expéditeur connue. C'est le cas courant.
- `siren` — le numéro figure dans une pièce jointe ou une signature, pas ailleurs.
- `semantique` — ni référence, ni adresse connue (le client écrit depuis son
  téléphone perso) : il ne reste que le contenu.
- `ambigu` — le client a plusieurs contrats susceptibles de correspondre. Le bon
  comportement n'est pas de deviner : c'est de demander.
- `inconnu` — l'expéditeur n'est pas au portefeuille. Un rattachement, ici, est
  une faute.

Chaque courriel porte donc, en en-tête `X-Attendu-*`, ce que le traitement DEVRAIT
en faire. C'est un jeu d'évaluation déguisé en boîte mail, et c'est ce qui permet
de mesurer le rattachement au lieu de le regarder marcher sur trois exemples.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from entities import (  # noqa: E402
    CABINET,
    CLIENTS_BY_ID,
    CONTRATS_BY_REF,
    PIECES_PAR_CLE,
    SEED,
)

# Horodatage de base. Figé, et non « maintenant » : une boîte dont les dates
# changent à chaque génération rendrait toute mesure irreproductible.
BASE = (2026, 7, 27)


@dataclass
class Piece:
    """Pièce jointe. Le contenu est du texte : ce POC ne fait pas d'OCR, et
    prétendre le contraire avec un faux PDF n'apprendrait rien."""

    filename: str
    content: str
    mimetype: str = "text/plain"


@dataclass
class Courriel:
    id: str
    de: str
    sujet: str
    corps: str
    jour: int
    heure: int
    difficulte: str
    intention: str
    client: str | None
    contrat: str | None = None
    intentions_tolerees: tuple[str, ...] = ()
    """Qualifications que deux gestionnaires compétents pourraient l'un et
    l'autre défendre sur ce courriel.

    **À n'employer qu'à contrecœur, et toujours avec la raison écrite.** C'est le
    pendant de la catégorie `tolerees` des pièces : sans elle, on mesure la
    capacité du modèle à deviner NOTRE arbitrage sur un cas réellement
    discutable, et on finit par ajuster le prompt à nos propres étiquettes — ce
    qui produit un score excellent et sans valeur. Avec elle, chaque tolérance
    est visible et discutable."""
    pieces: list[Piece] = field(default_factory=list)
    section: str | None = None
    """Clé de la section du référentiel qui s'applique. `None` = aucune."""
    manquantes: tuple[str, ...] = ()
    """Pièces que le traitement DOIT réclamer : clairement absentes du message.

    Le rappel se mesure là-dessus, et il doit pouvoir atteindre 100 %. Une pièce
    dont la présence se discute n'a rien à faire ici : elle irait dans
    `tolerees`, sinon on mesurerait la capacité du modèle à deviner notre avis
    plutôt que sa capacité à détecter une absence."""
    tolerees: tuple[str, ...] = ()
    """Pièces du référentiel arguablement fournies dans le corps du message.

    Les réclamer n'est pas une faute, ne pas les réclamer non plus. Sans cette
    catégorie, tout jugement limite serait compté comme une invention — et le
    chiffre des inventions cesserait de désigner ce qu'il doit désigner : une
    pièce sortie de nulle part."""


def _client_mail(client_id: str) -> tuple[str, str]:
    client = CLIENTS_BY_ID[client_id]
    return client.contact, client.email


COURRIELS: list[Courriel] = []


def mail(**kwargs) -> None:
    COURRIELS.append(Courriel(**kwargs))


# --- Cas faciles : la référence est écrite ------------------------------------

mail(
    id="0001",
    de="s.kervella@bativert.fr",
    sujet="Attestation décennale pour chantier Rezé",
    corps="""Bonjour,

Le maître d'ouvrage du chantier de la rue des Peupliers me réclame une
attestation décennale nominative avant l'ouverture, prévue le 10 août.

C'est bien sur le contrat DEC-2024-0117. Le chantier porte sur du gros œuvre
classique, rien de particulier.

Pouvez-vous me la sortir cette semaine ?

Bien cordialement,
Sandrine Kervella
Gérante — SARL BATIVERT""",
    jour=27,
    heure=8,
    difficulte="reference",
    intention="certificate_request",
    client="bativert",
    contrat="DEC-2024-0117",
)

mail(
    id="0002",
    de="direction@tourneix-transports.fr",
    sujet="Sortie de véhicule flotte FLA-2023-0088",
    corps="""Bonjour,

Nous avons vendu le porteur immatriculé FT-482-QM la semaine dernière.
Merci de le sortir du contrat FLA-2023-0088.

La cession a été faite le 22 juillet.

Cordialement,
Marc Tourneix""",
    jour=27,
    heure=9,
    difficulte="reference",
    intention="contract_amendment",
    client="tourneix",
    contrat="FLA-2023-0088",
)

# --- Cas courant : rien que l'adresse -----------------------------------------

mail(
    id="0003",
    de="f.aouad@lumigraph.fr",
    sujet="Question sur la reprise après l'incident",
    corps="""Bonjour Aurélie,

Une question suite au sinistre du mois de mars : l'assureur nous a imposé une
sauvegarde hors ligne hebdomadaire et un test de restauration tous les trois
mois.

Est-ce qu'on doit vous fournir une preuve de ces tests, ou est-ce que c'est
seulement en cas de nouveau sinistre qu'ils la demanderont ?

Merci,
Fabrice""",
    jour=27,
    heure=10,
    difficulte="domaine",
    intention="coverage_question",
    client="lumigraph",
    contrat="CYB-2026-0511",
)

mail(
    id="0004",
    de="contact@petrin-orvault.fr",
    sujet="URGENT - dégât des eaux labo Bugallière",
    corps="""Bonjour,

On a une fuite importante au laboratoire de la Bugallière depuis cette nuit.
Le faux plafond est tombé sur le plan de travail et le pétrin est sous l'eau.

On ne peut plus produire. J'ai coupé l'eau et l'électricité.

Qu'est-ce que je fais ?

Yannis Delaunay""",
    jour=27,
    heure=6,
    difficulte="domaine",
    intention="claim",
    client="petrin",
    contrat="MRP-2024-0295",
)

mail(
    id="0005",
    de="i.berthomieu@novaflux.io",
    sujet="Re: mise en cause client - nouvelle pièce",
    corps="""Bonjour,

Le client qui nous met en cause a transmis son chiffrage détaillé. Je vous le
joins.

Il passe de 58 000 € à 71 000 € en incluant des pertes d'exploitation qu'il
n'avait pas mentionnées.

Est-ce qu'il faut prévenir l'assureur de cette évolution ?

Inès Berthomieu
DAF — NOVAFLUX""",
    jour=27,
    heure=11,
    difficulte="domaine",
    intention="claim",
    client="novaflux",
    contrat="RCP-2025-0455",
    pieces=[
        Piece(
            "chiffrage-prejudice.txt",
            "CHIFFRAGE DU PREJUDICE\n\n"
            "Regression de facturation - montee de version du 12/05/2026\n\n"
            "Refacturation manuelle .............. 18 400 EUR\n"
            "Avoirs clients ...................... 24 100 EUR\n"
            "Perte d'exploitation (9 jours) ...... 28 500 EUR\n"
            "TOTAL ............................... 71 000 EUR\n",
        )
    ],
)

# --- Le SIREN est la seule accroche -------------------------------------------

mail(
    id="0006",
    de="comptabilite@ext-prestataire-paie.fr",
    sujet="Demande d'attestation pour dossier client",
    corps="""Bonjour,

Nous intervenons en tant que cabinet de paie pour l'un de vos assurés et devons
constituer un dossier de sous-traitance.

L'entreprise concernée est immatriculée sous le SIREN 879 044 512.

Merci de nous adresser une attestation de RC professionnelle en cours de
validité.

Service comptabilité""",
    jour=27,
    heure=14,
    difficulte="siren",
    intention="certificate_request",
    client="voltis",
    contrat="RCP-2024-0145",
)

mail(
    id="0007",
    de="p.nguessan.perso@gmail.com",
    sujet="Suite incendie benne",
    corps="""Bonjour,

Je vous écris depuis mon adresse personnelle, la messagerie de la société est
en panne depuis ce matin.

Le rapport des pompiers concernant l'incendie de la benne est arrivé. Je le
joins. Ils confirment l'origine électrique liée à une batterie au lithium.

Notre SIREN est le 490 118 735 si besoin.

Patrice N'Guessan
Directeur d'exploitation""",
    jour=27,
    heure=15,
    difficulte="siren",
    intention="claim",
    client="hexatri",
    contrat="FLA-2024-0277",
    pieces=[
        Piece(
            "rapport-pompiers.txt",
            "SDIS 44 - RAPPORT D'INTERVENTION\n\n"
            "Date : 06/05/2026 - 09h12\n"
            "Lieu : zone industrielle de Trignac\n"
            "Vehicule : benne a ordures menageres GH-905-TR\n\n"
            "Origine probable : emballement thermique d'un accumulateur\n"
            "lithium-ion present dans le chargement.\n"
            "Aucune victime. Vehicule detruit.\n",
        )
    ],
)

# --- Ni référence, ni adresse connue : le contenu, et rien d'autre ------------

mail(
    id="0008",
    de="nadia.bremond@orange.fr",
    sujet="terrasse",
    corps="""Bonjour,

Je vous écris de mon téléphone, désolée pour la mise en forme.

On voudrait garder la terrasse ouverte jusqu'à fin novembre cette année au lieu
de fin octobre, la météo est bonne et on a de la demande.

Est-ce que c'est possible côté assurance ? C'est le restaurant de La Baule.

Merci
Nadia""",
    jour=27,
    heure=17,
    difficulte="semantique",
    intention="contract_amendment",
    client="sablier",
    contrat="MRP-2025-0366",
)

mail(
    id="0009",
    de="atelier@fermetal-production.net",
    sujet="Cisaille n°3 - reprise de production",
    corps="""Bonjour,

La cisaille guillotine numéro 3 est réparée et a redémarré lundi.

L'expert avait retenu six jours d'immobilisation mais on est finalement resté
arrêté onze jours ouvrés, le variateur de remplacement a mis plus longtemps que
prévu à arriver.

Est-ce qu'on peut faire réévaluer la perte d'exploitation ?

Service atelier — FERMETAL INDUSTRIE""",
    jour=27,
    heure=16,
    difficulte="semantique",
    intention="claim",
    client="fermetal",
    contrat="DAB-2023-0061",
)

# --- Ambigu : plusieurs contrats possibles ------------------------------------

mail(
    id="0010",
    de="b.lecoutre@fermetal.fr",
    sujet="Augmentation de nos garanties",
    corps="""Bonjour,

Suite au conseil d'administration, nous souhaitons revoir nos plafonds à la
hausse. L'activité a progressé de 20 % cette année.

Pouvez-vous nous faire une proposition ?

Bruno Lecoutre
Directeur général""",
    jour=27,
    heure=12,
    difficulte="ambigu",
    intention="contract_amendment",
    client="fermetal",
    contrat=None,
)

mail(
    id="0011",
    de="c.ouldamara@agence-lamarelle.fr",
    sujet="Renouvellement",
    corps="""Bonjour Thomas,

On approche de l'échéance. Pouvez-vous nous faire un point sur ce qu'on paie
aujourd'hui et sur ce qui pourrait être renégocié ?

Claire""",
    jour=27,
    heure=13,
    difficulte="ambigu",
    intention="administrative",
    # « Faites-nous un point sur ce qu'on paie et ce qui se renégocie » : une
    # préparation d'échéance pour l'un, une question de cotisation pour l'autre.
    # Aucun acte n'est encore demandé, les deux lectures se défendent.
    intentions_tolerees=("billing_or_payment",),
    client="marelle",
    contrat=None,
)

# --- L'expéditeur n'est pas au portefeuille -----------------------------------

mail(
    id="0012",
    de="direction@charcuterie-blanchard.fr",
    sujet="Demande de devis multirisque",
    corps="""Bonjour,

Nous sommes une charcuterie artisanale de 8 salariés à Ancenis et cherchons à
changer d'assureur pour notre multirisque professionnelle.

Notre contrat actuel arrive à échéance au 1er octobre. Chiffre d'affaires
940 000 €, deux laboratoires et un point de vente.

Seriez-vous en mesure de nous faire une proposition ?

Cordialement,
Jean-Marc Blanchard""",
    jour=27,
    heure=18,
    difficulte="inconnu",
    intention="new_quote",
    client=None,
)

mail(
    id="0013",
    de="newsletter@assurpro-mag.fr",
    sujet="[AssurPro Mag] Les 10 tendances du courtage en 2027",
    corps="""Bonjour,

Découvrez notre dossier spécial : intelligence artificielle, distribution,
réglementation DDA... tout ce qui va changer pour les courtiers en 2027.

Lire le dossier en ligne.

Pour vous désabonner, cliquez ici.""",
    jour=27,
    heure=7,
    difficulte="inconnu",
    intention="hors_perimetre",
    client=None,
)

mail(
    id="0014",
    de="e.chapuis@plomberie-chapuis.fr",
    sujet="PAC installée avant l'avenant",
    corps="""Bonjour,

Question un peu gênante. On a posé une pompe à chaleur air/eau chez un client
fin avril, donc avant la date de l'avenant du 6 mai.

Le client vient de nous signaler un défaut sur l'installation.

Est-ce qu'on est couvert ou pas ?

Éric Chapuis""",
    jour=27,
    heure=9,
    difficulte="domaine",
    intention="coverage_question",
    # « Le client signale un défaut, est-ce qu'on est couvert ? » : une question
    # de garantie pour l'un, un sinistre décennal en gestation à déclarer sans
    # attendre pour l'autre. Le second n'est pas moins défendable que le premier.
    intentions_tolerees=("claim",),
    client="chapuis",
    contrat="DEC-2025-0188",
)

mail(
    id="0015",
    de="olivier.ducastel@garage-ducastel.fr",
    sujet="Véhicule client volé sur le parking",
    corps="""Bonjour,

Un véhicule client qui était en gardiennage chez nous a disparu cette nuit.
C'est un utilitaire, valeur environ 32 000 €.

J'ai porté plainte ce matin. Le portail était forcé.

Le client est furieux, il me demande de le rembourser tout de suite.
Je lui dis quoi ?

Olivier Ducastel""",
    jour=27,
    heure=8,
    difficulte="domaine",
    intention="claim",
    client="garageduc",
    contrat="RCP-2025-0318",
)


# --- Écriture -----------------------------------------------------------------


def _build(courriel: Courriel) -> EmailMessage:
    message = EmailMessage()

    # Le nom d'affichage n'est mis QUE si l'expéditeur est bien le contact connu
    # du client. Le coller sur l'adresse d'un tiers — un cabinet de paie, une
    # adresse personnelle — donnerait au traitement la réponse qu'il est censé
    # trouver, et le cas « siren » ou « semantique » cesserait d'en être un.
    nom = None
    if courriel.client:
        contact, adresse = _client_mail(courriel.client)
        if adresse.lower() == courriel.de.lower():
            nom = contact

    message["From"] = f"{nom} <{courriel.de}>" if nom else courriel.de
    message["To"] = f"gestion@{CABINET['domaine']}"
    message["Subject"] = courriel.sujet
    # Identifiant construit à la main : `make_msgid` tire de l'aléatoire et lit
    # l'horloge, ce qui suffirait à rendre la boîte différente à chaque
    # génération — et donc les mesures incomparables.
    message["Message-ID"] = (
        f"<{BASE[0]}{BASE[1]:02d}{courriel.jour:02d}."
        f"{courriel.id}@{CABINET['domaine']}>"
    )

    from datetime import UTC, datetime

    message["Date"] = format_datetime(
        datetime(BASE[0], BASE[1], courriel.jour, courriel.heure, 0, tzinfo=UTC)
    )

    # Vérité terrain, en en-tête X- : lisible par le harnais de mesure, ignorée
    # par tout client de messagerie, et invisible du corps que le modèle analyse.
    message["X-Attendu-Difficulte"] = courriel.difficulte
    message["X-Attendu-Intention"] = courriel.intention
    message["X-Attendu-Intention-Tolerees"] = " | ".join(courriel.intentions_tolerees)
    message["X-Attendu-Client"] = courriel.client or ""
    message["X-Attendu-Contrat"] = courriel.contrat or ""
    message["X-Attendu-Section"] = courriel.section or ""
    message["X-Attendu-Pieces"] = " | ".join(courriel.manquantes)
    message["X-Attendu-Pieces-Tolerees"] = " | ".join(courriel.tolerees)

    message.set_content(courriel.corps)
    for piece in courriel.pieces:
        maintype, subtype = piece.mimetype.split("/", 1)
        message.add_attachment(
            piece.content.encode("utf-8"),
            maintype=maintype,
            subtype=subtype,
            filename=piece.filename,
        )

    # `add_attachment` tire une frontière MIME aléatoire. C'est sans conséquence
    # pour un client de messagerie, et rédhibitoire ici : deux générations
    # produiraient des octets différents, donc des mesures incomparables et une
    # boîte qu'on ne peut pas versionner utilement.
    if message.is_multipart():
        message.set_boundary(f"----=_frontiere_{courriel.id}")
    return message


def _verifier() -> None:
    """Un courriel qui pointe sur un contrat inexistant fausserait la mesure."""
    for courriel in COURRIELS:
        if courriel.client and courriel.client not in CLIENTS_BY_ID:
            raise SystemExit(f"{courriel.id} : client inconnu {courriel.client}")
        if courriel.contrat and courriel.contrat not in CONTRATS_BY_REF:
            raise SystemExit(f"{courriel.id} : contrat inconnu {courriel.contrat}")
        if courriel.contrat and not courriel.client:
            raise SystemExit(f"{courriel.id} : contrat attendu sans client attendu")
        if courriel.difficulte == "inconnu" and courriel.client:
            raise SystemExit(
                f"{courriel.id} : marqué « inconnu » mais rattaché à {courriel.client}"
            )

    identifiants = [courriel.id for courriel in COURRIELS]
    if len(set(identifiants)) != len(identifiants):
        raise SystemExit("identifiants de courriel en double")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="mailbox", help="dossier de la boîte")
    args = parser.parse_args()

    _verifier()

    racine = Path(args.out)
    if racine.exists():
        shutil.rmtree(racine)
    racine.mkdir(parents=True)

    par_difficulte: dict[str, int] = {}
    for courriel in COURRIELS:
        chemin = racine / f"{courriel.id}-{courriel.difficulte}.eml"
        chemin.write_bytes(_build(courriel).as_bytes())
        par_difficulte[courriel.difficulte] = par_difficulte.get(courriel.difficulte, 0) + 1

    pieces = sum(len(courriel.pieces) for courriel in COURRIELS)
    print(f"{len(COURRIELS)} courriels écrits dans {racine}/ ({pieces} pièces jointes)")
    for difficulte in ("reference", "domaine", "siren", "semantique", "ambigu", "inconnu"):
        if difficulte in par_difficulte:
            print(f"  {difficulte:<12} {par_difficulte[difficulte]:>3}")
    print(f"graine du corpus : {SEED} — la boîte est reproductible à l'octet près")



# --- Pièces attendues, par courriel -------------------------------------------
#
# **Trois catégories, et la distinction est ce qui rend la mesure honnête.**
#
#   exigées   pièce du référentiel CLAIREMENT absente du message et de ses
#             pièces jointes. Le rappel se mesure là-dessus, et il doit pouvoir
#             atteindre 100 % : si une pièce y figure alors que sa présence se
#             discute, on mesure la capacité du modèle à deviner notre avis.
#   tolérées  pièce du référentiel arguablement fournie dans le CORPS du message
#             (« le chantier de la rue des Peupliers » vaut objet du marché).
#             La réclamer n'est pas une faute, ne pas la réclamer non plus.
#   le reste  pièce de la section RÉELLEMENT fournie → la réclamer est une faute
#             (« réclamée à tort ») ; pièce hors référentiel → « inventée ».
#
# Sans la catégorie tolérée, tout jugement limite serait compté comme une
# invention, et le chiffre des inventions cesserait de désigner ce qu'il doit
# désigner : une pièce sortie de nulle part.

ATTENDU_PIECES: dict[str, tuple[str | None, tuple[str, ...], tuple[str, ...]]] = {
    # id : (section du référentiel, exigées, tolérées)
    "0001": (
        "certificate_request / assuré",
        (),
        ("objet précis du marché ou du chantier visé", "établissement ou activité à mentionner"),
    ),
    "0002": (
        "contract_amendment / véhicule",
        ("certificat de cession",),
        # Sortie de flotte : les pièces d'entrée n'ont pas d'objet, mais les
        # réclamer est une maladresse, pas une invention.
        (
            "carte grise du véhicule",
            "date de première mise en circulation",
            "usage du véhicule et nom du conducteur principal",
        ),
    ),
    # Question de garantie : aucune pièce à réunir, rien à réclamer.
    "0003": (None, (), ()),
    "0004": (
        "claim / dégât des eaux",
        (
            "constat amiable dégât des eaux",
            "facture de recherche de fuite",
            "devis de remise en état",
            "photographies des dommages",
            "coordonnées du tiers ou du syndic",
        ),
        (),
    ),
    "0005": (
        "claim / mise en cause",
        (
            "courrier de réclamation du tiers",
            "contrat ou devis liant l'assuré au réclamant",
            "échanges antérieurs avec le réclamant",
        ),
        (),
    ),
    "0006": (
        "certificate_request / tiers",
        ("mandat écrit de l'assuré autorisant la communication",),
        ("objet précis du marché ou du contrat visé",),
    ),
    "0007": (
        "claim / incendie",
        (
            "photographies des dommages",
            "carte grise et dernier contrôle technique du véhicule concerné",
            "inventaire chiffré des biens détruits",
        ),
        (),
    ),
    "0008": (
        "contract_amendment / période",
        (
            "capacité d'accueil ou surface concernée",
            "mesures de protection appliquées hors saison",
        ),
        ("dates exactes de la période demandée",),
    ),
    "0009": (
        "claim / bris de machine",
        (
            "rapport du technicien sur la cause de la panne",
            "facture de la pièce ou de la réparation",
            "justificatif de la durée d'immobilisation",
        ),
        ("contrat d'entretien de la machine",),
    ),
    "0010": (
        "contract_amendment / capitaux",
        (
            "valeur actualisée des biens ou des capitaux à garantir",
            "justificatif de la variation demandée",
        ),
        (),
    ),
    # Demande de point de gestion : aucune pièce exigible.
    "0011": (None, (), ()),
    "0012": (
        "new_quote",
        (
            "extrait Kbis de moins de trois mois",
            "dernier bilan clos",
            "relevé de sinistralité des cinq derniers exercices",
        ),
        ("descriptif des locaux", "liste des activités réellement exercées"),
    ),
    # Infolettre.
    "0013": (None, (), ()),
    # Question de garantie.
    "0014": (None, (), ()),
    "0015": (
        "claim / véhicule confié",
        (
            "dépôt de plainte",
            "ordre de réparation ou preuve de la remise du véhicule",
            "valeur du véhicule et facture d'achat",
            "photographies des points d'effraction",
        ),
        (),
    ),
}


def _appliquer_pieces() -> None:
    """Reporte la table sur les courriels, en vérifiant qu'elle tient debout.

    Les trois contrôles ci-dessous ferment les trois façons dont un jeu
    d'évaluation ment sans qu'on s'en aperçoive : une pièce attendue qui
    n'existe pas au référentiel (rappel plafonné à jamais), une pièce classée
    deux fois (comptée deux fois), un courriel oublié (dénominateur faux).
    """
    for courriel in COURRIELS:
        if courriel.id not in ATTENDU_PIECES:
            raise SystemExit(f"{courriel.id} : aucune attente de pièces déclarée")

        section, exigees, tolerees = ATTENDU_PIECES[courriel.id]
        if section is not None:
            connues = PIECES_PAR_CLE.get(section)
            if connues is None:
                raise SystemExit(f"{courriel.id} : section inconnue « {section} »")
            for piece in (*exigees, *tolerees):
                if piece not in connues:
                    raise SystemExit(
                        f"{courriel.id} : « {piece} » ne figure pas dans la section "
                        f"« {section} » du référentiel — elle serait inatteignable"
                    )
        elif exigees or tolerees:
            raise SystemExit(f"{courriel.id} : pièces attendues sans section")

        if set(exigees) & set(tolerees):
            raise SystemExit(f"{courriel.id} : pièce à la fois exigée et tolérée")

        courriel.section = section
        courriel.manquantes = exigees
        courriel.tolerees = tolerees


_appliquer_pieces()


# Le point d'entrée vit en FIN de fichier, après `_appliquer_pieces()` :
# placé plus haut, `main()` écrirait la boîte avant que la table des pièces
# ait été reportée sur les courriels, et les en-têtes attendus seraient vides.
if __name__ == "__main__":
    main()
