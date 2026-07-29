"""Données de référence du cabinet de courtage fictif.

Tout est **inventé** : aucune raison sociale, aucun SIREN, aucune adresse ne
correspond à une entreprise réelle. Les SIREN sont construits pour être
syntaxiquement plausibles mais échouent volontairement la clé de Luhn, afin
qu'aucun d'eux ne puisse collisionner avec un numéro attribué.

Ce module ne contient que des faits **structurés**. La mise en mots — et donc la
variation lexicale qui empêche le jeu d'évaluation d'être trivial — vit dans
`generate.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Graine unique du corpus. Toute la génération en dépend : deux exécutions
# produisent des octets identiques, donc `make ingest` est idempotent et deux
# mesures d'évaluation sont comparables. La changer invalide toutes les mesures
# publiées — ne le faire qu'en connaissance de cause.
SEED = 20260729

CABINET = {
    "nom": "Cabinet Rivelaine & Associés",
    "forme": "SAS de courtage d'assurance",
    "orias": "24 001 887",
    "ville": "Nantes",
    "cp": "44000",
    "adresse": "12 quai Malakoff",
    "tel": "02 40 11 22 33",
    "domaine": "rivelaine-courtage.fr",
}


# --- Clients ------------------------------------------------------------------


@dataclass(frozen=True)
class Client:
    id: str
    raison: str
    siren: str
    ville: str
    cp: str
    naf: str
    activite: str
    effectif: int
    ca_keur: int
    contact: str
    contact_fonction: str
    email: str
    tel: str
    domaine: str

    @property
    def siret(self) -> str:
        return f"{self.siren}00027"


CLIENTS: tuple[Client, ...] = (
    Client(
        id="bativert",
        raison="SARL BATIVERT",
        siren="812 447 903",
        ville="Rezé",
        cp="44400",
        naf="43.99C",
        activite="travaux de maçonnerie générale et gros œuvre de bâtiment",
        effectif=18,
        ca_keur=2400,
        contact="Sandrine Kervella",
        contact_fonction="gérante",
        email="s.kervella@bativert.fr",
        tel="02 40 75 18 42",
        domaine="bativert.fr",
    ),
    Client(
        id="tourneix",
        raison="SAS TOURNEIX TRANSPORTS",
        siren="799 310 664",
        ville="Saint-Herblain",
        cp="44800",
        naf="49.41A",
        activite="transport routier de fret interurbain",
        effectif=34,
        ca_keur=5100,
        contact="Marc Tourneix",
        contact_fonction="président",
        email="direction@tourneix-transports.fr",
        tel="02 40 92 07 15",
        domaine="tourneix-transports.fr",
    ),
    Client(
        id="lumigraph",
        raison="SARL LUMIGRAPH",
        siren="524 806 271",
        ville="Nantes",
        cp="44200",
        naf="18.12Z",
        activite="imprimerie de labeur et façonnage",
        effectif=11,
        ca_keur=1350,
        contact="Fabrice Aouad",
        contact_fonction="gérant",
        email="f.aouad@lumigraph.fr",
        tel="02 51 83 44 90",
        domaine="lumigraph.fr",
    ),
    Client(
        id="ergomed",
        raison="SELARL ERGOMED CONSEIL",
        siren="843 129 550",
        ville="Nantes",
        cp="44000",
        naf="86.90F",
        activite="activités de santé au travail et d'ergonomie",
        effectif=7,
        ca_keur=690,
        contact="Dr Hélène Vasseur",
        contact_fonction="associée",
        email="h.vasseur@ergomed-conseil.fr",
        tel="02 40 48 61 07",
        domaine="ergomed-conseil.fr",
    ),
    Client(
        id="petrin",
        raison="SARL LE PÉTRIN D'ORVAULT",
        siren="football",  # remplacé plus bas — voir _fix
        ville="Orvault",
        cp="44700",
        naf="10.71C",
        activite="boulangerie et pâtisserie artisanale, deux points de vente",
        effectif=14,
        ca_keur=1180,
        contact="Yannis Delaunay",
        contact_fonction="gérant",
        email="contact@petrin-orvault.fr",
        tel="02 40 63 29 84",
        domaine="petrin-orvault.fr",
    ),
    Client(
        id="novaflux",
        raison="SAS NOVAFLUX",
        siren="901 552 348",
        ville="Carquefou",
        cp="44470",
        naf="62.02A",
        activite="conseil en systèmes et logiciels informatiques",
        effectif=23,
        ca_keur=3200,
        contact="Inès Berthomieu",
        contact_fonction="directrice administrative et financière",
        email="i.berthomieu@novaflux.io",
        tel="02 28 07 55 12",
        domaine="novaflux.io",
    ),
    Client(
        id="chapuis",
        raison="EURL PLOMBERIE CHAPUIS",
        siren="538 774 016",
        ville="Vertou",
        cp="44120",
        naf="43.22A",
        activite="travaux d'installation d'eau et de gaz en tous locaux",
        effectif=6,
        ca_keur=720,
        contact="Éric Chapuis",
        contact_fonction="gérant",
        email="e.chapuis@plomberie-chapuis.fr",
        tel="02 40 34 77 61",
        domaine="plomberie-chapuis.fr",
    ),
    Client(
        id="marelle",
        raison="SAS AGENCE LA MARELLE",
        siren="822 065 197",
        ville="Nantes",
        cp="44100",
        naf="68.31Z",
        activite="administration de biens et transaction immobilière",
        effectif=12,
        ca_keur=1450,
        contact="Claire Ould-Amara",
        contact_fonction="directrice",
        email="c.ouldamara@agence-lamarelle.fr",
        tel="02 51 72 08 33",
        domaine="agence-lamarelle.fr",
    ),
    Client(
        id="fermetal",
        raison="SAS FERMETAL INDUSTRIE",
        siren="411 293 806",
        ville="Couëron",
        cp="44220",
        naf="25.11Z",
        activite="fabrication de structures métalliques et de charpentes",
        effectif=29,
        ca_keur=4700,
        contact="Bruno Lecoutre",
        contact_fonction="directeur général",
        email="b.lecoutre@fermetal.fr",
        tel="02 40 86 13 55",
        domaine="fermetal.fr",
    ),
    Client(
        id="sablier",
        raison="SARL LE SABLIER",
        siren="753 601 428",
        ville="La Baule-Escoublac",
        cp="44500",
        naf="56.10A",
        activite="restauration traditionnelle avec terrasse saisonnière",
        effectif=16,
        ca_keur=1620,
        contact="Nadia Brémond",
        contact_fonction="gérante",
        email="direction@lesablier-labaule.fr",
        tel="02 40 60 91 07",
        domaine="lesablier-labaule.fr",
    ),
    Client(
        id="voltis",
        raison="SAS VOLTIS ÉLECTRICITÉ",
        siren="879 044 512",
        ville="Bouguenais",
        cp="44340",
        naf="43.21A",
        activite="travaux d'installation électrique en tous locaux",
        effectif=21,
        ca_keur=2950,
        contact="Steve Marcelin",
        contact_fonction="président",
        email="s.marcelin@voltis-elec.fr",
        tel="02 40 32 66 09",
        domaine="voltis-elec.fr",
    ),
    Client(
        id="hexatri",
        raison="SAS HEXATRI ENVIRONNEMENT",
        siren="490 118 735",
        ville="Trignac",
        cp="44570",
        naf="38.11Z",
        activite="collecte et tri de déchets non dangereux",
        effectif=27,
        ca_keur=3800,
        contact="Patrice N'Guessan",
        contact_fonction="directeur d'exploitation",
        email="p.nguessan@hexatri.fr",
        tel="02 40 45 82 20",
        domaine="hexatri.fr",
    ),
    Client(
        id="calliope",
        raison="SARL CALLIOPE FORMATION",
        siren="808 236 941",
        ville="Nantes",
        cp="44300",
        naf="85.59A",
        activite="formation continue d'adultes, présentiel et distanciel",
        effectif=9,
        ca_keur=840,
        contact="Léa Ferrandis",
        contact_fonction="cogérante",
        email="l.ferrandis@calliope-formation.fr",
        tel="02 51 89 30 41",
        domaine="calliope-formation.fr",
    ),
    Client(
        id="garageduc",
        raison="SARL GARAGE DUCASTEL",
        siren="632 470 189",
        ville="Sainte-Luce-sur-Loire",
        cp="44980",
        naf="45.20A",
        activite="entretien et réparation de véhicules légers, dépannage 24 h",
        effectif=13,
        ca_keur=1930,
        contact="Olivier Ducastel",
        contact_fonction="gérant",
        email="contact@garage-ducastel.fr",
        tel="02 40 25 14 76",
        domaine="garage-ducastel.fr",
    ),
)


def _fix() -> tuple[Client, ...]:
    """Corrige l'entrée volontairement invalide laissée ci-dessus.

    Elle sert de garde-fou : si quelqu'un copie ce fichier sans lire, le SIREN
    absurde se remarque immédiatement à la première génération.
    """
    import dataclasses

    return tuple(
        dataclasses.replace(client, siren="443 918 072") if client.siren == "football" else client
        for client in CLIENTS
    )


CLIENTS = _fix()
CLIENTS_BY_ID = {client.id: client for client in CLIENTS}


# --- Produits -----------------------------------------------------------------


@dataclass(frozen=True)
class Produit:
    id: str
    label: str
    court: str
    prefixe: str
    obligatoire: bool
    garanties: tuple[str, ...]
    exclusions: tuple[str, ...]


PRODUITS: tuple[Produit, ...] = (
    Produit(
        id="rc_pro",
        label="Responsabilité Civile Professionnelle",
        court="RC Pro",
        prefixe="RCP",
        obligatoire=False,
        garanties=(
            "responsabilité civile exploitation",
            "responsabilité civile après livraison et après achèvement des travaux",
            "faute professionnelle et manquement au devoir de conseil",
            "atteinte accidentelle à l'environnement",
            "défense pénale et recours suite à sinistre",
        ),
        exclusions=(
            "les dommages causés intentionnellement par l'assuré ou avec sa complicité",
            "les amendes, pénalités contractuelles et sanctions administratives",
            "les dommages relevant d'une garantie décennale obligatoire",
            "les activités non déclarées aux conditions particulières",
        ),
    ),
    Produit(
        id="mrp",
        label="Multirisque Professionnelle",
        court="MRP",
        prefixe="MRP",
        obligatoire=False,
        garanties=(
            "incendie, explosion, chute de la foudre et dommages électriques",
            "dégâts des eaux et gel des canalisations",
            "vol, vandalisme et détérioration mobilière",
            "bris de glace et enseignes",
            "perte d'exploitation consécutive à un sinistre garanti",
            "responsabilité civile occupant des locaux",
        ),
        exclusions=(
            "les dommages résultant d'un défaut d'entretien caractérisé",
            "les marchandises stockées à l'extérieur sans protection",
            "les vols commis sans effraction ni violence",
            "les biens confiés non déclarés",
        ),
    ),
    Produit(
        id="flotte",
        label="Flotte Automobile",
        court="flotte auto",
        prefixe="FLA",
        obligatoire=True,
        garanties=(
            "responsabilité civile circulation, sans limitation pour les dommages corporels",
            "dommages tous accidents",
            "vol, incendie et tentative de vol",
            "bris de glaces",
            "assistance 0 km et véhicule de remplacement",
            "protection juridique du conducteur",
        ),
        exclusions=(
            "la conduite sans permis valide ou sous l'empire d'un état alcoolique",
            "l'usage en compétition ou en épreuve de vitesse",
            "le transport de matières dangereuses non déclaré",
            "les dommages aux marchandises transportées, objet d'un contrat distinct",
        ),
    ),
    Produit(
        id="decennale",
        label="Responsabilité Civile Décennale",
        court="décennale",
        prefixe="DEC",
        obligatoire=True,
        garanties=(
            "responsabilité décennale au titre des articles 1792 et suivants du Code civil",
            "bon fonctionnement des éléments d'équipement dissociables (garantie biennale)",
            "dommages immatériels consécutifs à un désordre garanti",
            "responsabilité civile en cours de chantier",
        ),
        exclusions=(
            "les travaux réalisés en dehors des activités déclarées",
            "les désordres esthétiques sans atteinte à la solidité ni à la destination",
            "les ouvrages soumis à une technique non courante sans avis technique",
            "l'usure normale et le défaut d'entretien de l'ouvrage",
        ),
    ),
    Produit(
        id="cyber",
        label="Cyber-risques",
        court="cyber",
        prefixe="CYB",
        obligatoire=False,
        garanties=(
            "frais de réponse à incident et cellule de crise 24/7",
            "reconstitution des données et remise en service des systèmes",
            "perte d'exploitation consécutive à une interruption de service",
            "responsabilité civile pour violation de données personnelles",
            "frais de notification aux personnes concernées et à la CNIL",
            "fraude au virement et fraude au président",
        ),
        exclusions=(
            "les incidents résultant d'un système non mis à jour depuis plus de six mois",
            "les rançons versées sans accord préalable de l'assureur",
            "les pertes de valeur d'actifs numériques et de crypto-actifs",
            "les incidents connus de l'assuré avant la prise d'effet du contrat",
        ),
    ),
    Produit(
        id="dab",
        label="Dommages aux Biens",
        court="dommages aux biens",
        prefixe="DAB",
        obligatoire=False,
        garanties=(
            "incendie et événements assimilés",
            "événements climatiques, tempête, grêle et poids de la neige",
            "catastrophes naturelles selon arrêté interministériel",
            "bris de machine et matériel de production",
            "marchandises en entrepôt et en chambre froide",
        ),
        exclusions=(
            "les bâtiments en cours de construction non déclarés",
            "les dommages de nature esthétique sans altération de fonction",
            "le matériel de plus de quinze ans pour la garantie bris de machine",
            "les biens situés hors des adresses déclarées",
        ),
    ),
)

PRODUITS_BY_ID = {produit.id: produit for produit in PRODUITS}


# --- Compagnies ---------------------------------------------------------------


@dataclass(frozen=True)
class Compagnie:
    id: str
    nom: str
    gestionnaire: str
    email: str
    delai_attestation_j: int
    delai_avenant_j: int


COMPAGNIES: tuple[Compagnie, ...] = (
    Compagnie(
        id="artemis",
        nom="Artémis Assurances",
        gestionnaire="Pôle courtage Ouest",
        email="courtage-ouest@artemis-assurances.fr",
        delai_attestation_j=2,
        delai_avenant_j=8,
    ),
    Compagnie(
        id="norvalis",
        nom="Norvalis IARD",
        gestionnaire="Service production entreprises",
        email="production.entreprises@norvalis-iard.fr",
        delai_attestation_j=5,
        delai_avenant_j=15,
    ),
    Compagnie(
        id="mutuas",
        nom="Mutuas Professionnels",
        gestionnaire="Cellule courtiers partenaires",
        email="partenaires@mutuas-pro.fr",
        delai_attestation_j=1,
        delai_avenant_j=10,
    ),
    Compagnie(
        id="orlane",
        nom="Orlane Risques Spéciaux",
        gestionnaire="Souscription grands comptes",
        email="souscription@orlane-rs.fr",
        delai_attestation_j=7,
        delai_avenant_j=21,
    ),
)

COMPAGNIES_BY_ID = {compagnie.id: compagnie for compagnie in COMPAGNIES}


# --- Collaborateurs du cabinet ------------------------------------------------


@dataclass(frozen=True)
class Collaborateur:
    nom: str
    role: str
    groupe: str
    email: str


COLLABORATEURS: tuple[Collaborateur, ...] = (
    Collaborateur("Aurélie Pichon", "responsable gestion", "gestion", "a.pichon"),
    Collaborateur("Karim Belhadj", "chargé de production", "production", "k.belhadj"),
    Collaborateur("Sonia Retailleau", "gestionnaire sinistres", "sinistres", "s.retailleau"),
    Collaborateur("Thomas Guérineau", "chargé de clientèle", "gestion", "t.guerineau"),
    Collaborateur("Nathalie Ory", "directrice associée", "direction", "n.ory"),
)


# --- Contrats du portefeuille -------------------------------------------------
#
# Le portefeuille est décrit ici en extension plutôt que tiré au sort : chaque
# ligne est un FAIT que le jeu d'évaluation pourra interroger. Un portefeuille
# aléatoire rendrait les questions ingérables à maintenir.


@dataclass(frozen=True)
class Contrat:
    reference: str
    client_id: str
    produit_id: str
    compagnie_id: str
    date_effet: str
    echeance_jour: str
    prime_ht: int
    franchise: int
    plafond_keur: int
    fractionnement: str
    particularites: tuple[str, ...] = field(default=())


CONTRATS: tuple[Contrat, ...] = (
    # BATIVERT — maçonnerie : décennale + RC Pro + MRP
    Contrat("DEC-2024-0117", "bativert", "decennale", "norvalis", "2024-04-01", "1er avril",
            9840, 1500, 3000, "trimestriel",
            ("activités déclarées : maçonnerie, béton armé, ravalement sans isolation",)),
    Contrat("RCP-2024-0118", "bativert", "rc_pro", "norvalis", "2024-04-01", "1er avril",
            2160, 800, 8000, "trimestriel"),
    Contrat("MRP-2025-0342", "bativert", "mrp", "artemis", "2025-09-01", "1er septembre",
            3420, 900, 1200, "annuel",
            ("dépôt de matériaux de 640 m² couvert, alarme reliée à un télésurveilleur",)),
    # TOURNEIX — transport : flotte + RC Pro + MRP
    Contrat("FLA-2023-0088", "tourneix", "flotte", "artemis", "2023-07-01", "1er juillet",
            48600, 1200, 0, "mensuel",
            ("flotte de 22 véhicules dont 14 poids lourds et 8 véhicules légers",
             "conducteurs désignés non nominatifs, permis de moins de 3 points retirés")),
    Contrat("RCP-2023-0089", "tourneix", "rc_pro", "artemis", "2023-07-01", "1er juillet",
            5900, 1500, 6000, "mensuel"),
    Contrat("MRP-2023-0090", "tourneix", "mrp", "artemis", "2023-07-01", "1er juillet",
            7250, 1500, 2500, "mensuel"),
    # LUMIGRAPH — imprimerie : MRP + DAB + cyber
    Contrat("MRP-2025-0401", "lumigraph", "mrp", "mutuas", "2025-01-01", "1er janvier",
            4180, 750, 1800, "semestriel",
            ("presse offset quadri de 2019 déclarée en valeur à neuf",)),
    Contrat("DAB-2025-0402", "lumigraph", "dab", "mutuas", "2025-01-01", "1er janvier",
            2960, 1000, 2200, "semestriel"),
    Contrat("CYB-2026-0511", "lumigraph", "cyber", "orlane", "2026-02-01", "1er février",
            3400, 5000, 500, "annuel",
            ("sauvegarde externalisée quotidienne exigée, testée deux fois par an",)),
    # ERGOMED — santé au travail : RC Pro + MRP
    Contrat("RCP-2025-0233", "ergomed", "rc_pro", "mutuas", "2025-06-01", "1er juin",
            3750, 500, 5000, "annuel",
            ("activité de conseil en ergonomie, hors actes médicaux de soin",)),
    Contrat("MRP-2025-0234", "ergomed", "mrp", "mutuas", "2025-06-01", "1er juin",
            1420, 400, 600, "annuel"),
    # LE PÉTRIN — boulangerie : MRP + DAB
    Contrat("MRP-2024-0295", "petrin", "mrp", "artemis", "2024-11-01", "1er novembre",
            5310, 600, 1500, "trimestriel",
            ("deux fonds de commerce distincts, Orvault centre et Orvault Bugallière",
             "chambres froides couvertes pour les denrées jusqu'à 18 000 €")),
    Contrat("DAB-2024-0296", "petrin", "dab", "artemis", "2024-11-01", "1er novembre",
            2140, 800, 900, "trimestriel"),
    # NOVAFLUX — éditeur : RC Pro + cyber + MRP
    Contrat("RCP-2025-0455", "novaflux", "rc_pro", "orlane", "2025-03-01", "1er mars",
            8200, 2500, 10000, "semestriel",
            ("garantie faute professionnelle étendue aux prestations d'intégration",)),
    Contrat("CYB-2025-0456", "novaflux", "cyber", "orlane", "2025-03-01", "1er mars",
            11500, 10000, 3000, "semestriel",
            ("authentification multifacteur obligatoire sur tous les accès distants",
             "plan de continuité testé annuellement, procès-verbal à fournir")),
    Contrat("MRP-2025-0457", "novaflux", "mrp", "orlane", "2025-03-01", "1er mars",
            2870, 750, 1100, "semestriel"),
    # CHAPUIS — plomberie : décennale + RC Pro
    Contrat("DEC-2025-0188", "chapuis", "decennale", "norvalis", "2025-05-01", "1er mai",
            4620, 1200, 2000, "trimestriel",
            ("activités déclarées : plomberie sanitaire, chauffage à eau chaude, "
             "hors pompes à chaleur géothermiques",)),
    Contrat("RCP-2025-0189", "chapuis", "rc_pro", "norvalis", "2025-05-01", "1er mai",
            1180, 500, 4000, "trimestriel"),
    # LA MARELLE — immobilier : RC Pro + MRP + cyber
    Contrat("RCP-2024-0212", "marelle", "rc_pro", "artemis", "2024-02-01", "1er février",
            6400, 1000, 7500, "annuel",
            ("garantie financière de gestion immobilière souscrite séparément",)),
    Contrat("MRP-2024-0213", "marelle", "mrp", "artemis", "2024-02-01", "1er février",
            1980, 500, 800, "annuel"),
    Contrat("CYB-2026-0530", "marelle", "cyber", "mutuas", "2026-01-01", "1er janvier",
            2750, 3000, 750, "annuel"),
    # FERMETAL — métallurgie : DAB + RC Pro + flotte + décennale
    Contrat("DAB-2023-0061", "fermetal", "dab", "norvalis", "2023-10-01", "1er octobre",
            18400, 3000, 6500, "trimestriel",
            ("atelier de 3 200 m², sprinklage complet contrôlé annuellement",)),
    Contrat("RCP-2023-0062", "fermetal", "rc_pro", "norvalis", "2023-10-01", "1er octobre",
            7100, 2000, 9000, "trimestriel"),
    Contrat("FLA-2024-0301", "fermetal", "flotte", "norvalis", "2024-10-01", "1er octobre",
            14200, 900, 0, "trimestriel",
            ("flotte de 9 véhicules utilitaires, aucun poids lourd",)),
    Contrat("DEC-2023-0063", "fermetal", "decennale", "norvalis", "2023-10-01", "1er octobre",
            12600, 2500, 4000, "trimestriel",
            ("activités déclarées : charpente et ossature métallique, bardage, serrurerie",)),
    # LE SABLIER — restaurant : MRP + RC Pro
    Contrat("MRP-2025-0366", "sablier", "mrp", "mutuas", "2025-04-01", "1er avril",
            4870, 700, 1300, "trimestriel",
            ("terrasse saisonnière de 60 couverts déclarée d'avril à octobre",
             "friteuses sous contrat d'entretien semestriel obligatoire")),
    Contrat("RCP-2025-0367", "sablier", "rc_pro", "mutuas", "2025-04-01", "1er avril",
            1640, 450, 4500, "trimestriel"),
    # VOLTIS — électricité : décennale + RC Pro + flotte
    Contrat("DEC-2024-0144", "voltis", "decennale", "artemis", "2024-06-01", "1er juin",
            8300, 1500, 2500, "trimestriel",
            ("activités déclarées : installation électrique courant fort et faible, "
             "bornes de recharge jusqu'à 22 kW",)),
    Contrat("RCP-2024-0145", "voltis", "rc_pro", "artemis", "2024-06-01", "1er juin",
            2540, 700, 6000, "trimestriel"),
    Contrat("FLA-2025-0390", "voltis", "flotte", "artemis", "2025-06-01", "1er juin",
            9700, 800, 0, "trimestriel",
            ("flotte de 11 véhicules légers aménagés",)),
    # HEXATRI — déchets : flotte + RC Pro + DAB
    Contrat("FLA-2024-0277", "hexatri", "flotte", "orlane", "2024-09-01", "1er septembre",
            37800, 1500, 0, "mensuel",
            ("flotte de 17 véhicules dont 12 bennes à ordures ménagères",)),
    Contrat("RCP-2024-0278", "hexatri", "rc_pro", "orlane", "2024-09-01", "1er septembre",
            9200, 3000, 8000, "mensuel",
            ("garantie atteinte à l'environnement portée à 2 000 000 € par sinistre",)),
    Contrat("DAB-2024-0279", "hexatri", "dab", "orlane", "2024-09-01", "1er septembre",
            11300, 2500, 4200, "mensuel"),
    # CALLIOPE — formation : RC Pro + MRP
    Contrat("RCP-2026-0602", "calliope", "rc_pro", "mutuas", "2026-01-01", "1er janvier",
            1890, 400, 3000, "annuel",
            ("formation en présentiel et distanciel, hors organisme certificateur",)),
    Contrat("MRP-2026-0603", "calliope", "mrp", "mutuas", "2026-01-01", "1er janvier",
            1250, 350, 500, "annuel"),
    # GARAGE DUCASTEL — réparation auto : RC Pro + MRP + flotte
    Contrat("RCP-2025-0318", "garageduc", "rc_pro", "norvalis", "2025-08-01", "1er août",
            4300, 900, 5000, "trimestriel",
            ("garantie véhicules confiés à hauteur de 400 000 € tous véhicules confondus",)),
    Contrat("MRP-2025-0319", "garageduc", "mrp", "norvalis", "2025-08-01", "1er août",
            3980, 800, 1400, "trimestriel"),
    Contrat("FLA-2025-0320", "garageduc", "flotte", "norvalis", "2025-08-01", "1er août",
            6100, 700, 0, "trimestriel",
            ("2 dépanneuses et 4 véhicules de courtoisie, garage W déclaré",)),
)

CONTRATS_BY_REF = {contrat.reference: contrat for contrat in CONTRATS}


def contrats_du_client(client_id: str) -> list[Contrat]:
    return [contrat for contrat in CONTRATS if contrat.client_id == client_id]


# --- Sinistres ----------------------------------------------------------------


@dataclass(frozen=True)
class Sinistre:
    reference: str
    contrat_ref: str
    date_survenance: str
    date_declaration: str
    nature: str
    circonstances: str
    montant_estime: int
    statut: str
    expert: str | None = None


SINISTRES: tuple[Sinistre, ...] = (
    Sinistre("SIN-2026-0041", "MRP-2025-0342", "2026-01-18", "2026-01-19",
             "dégât des eaux",
             "rupture d'un flexible d'alimentation sous l'évier du local social pendant "
             "le week-end ; l'eau a gagné le bureau attenant et le stock de sacs de liant",
             8400, "expertise en cours", "Cabinet Vaugrenard"),
    Sinistre("SIN-2026-0052", "FLA-2023-0088", "2026-02-03", "2026-02-03",
             "collision",
             "poids lourd immatriculé FT-482-QM heurté par l'arrière à un feu rouge "
             "boulevard de Doulon ; tiers identifié, constat amiable signé",
             12600, "en attente d'accord du tiers", None),
    Sinistre("SIN-2026-0067", "CYB-2026-0511", "2026-03-11", "2026-03-11",
             "rançongiciel",
             "chiffrement du serveur de production et du NAS de sauvegarde ; "
             "arrêt de la chaîne prépresse pendant quatre jours ouvrés",
             46000, "cellule de crise activée", "Groupe Sentinelle Forensics"),
    Sinistre("SIN-2025-0388", "DEC-2024-0117", "2025-11-22", "2025-11-27",
             "désordre décennal",
             "fissuration traversante en façade sud d'une maison individuelle livrée "
             "en 2023 ; infiltration constatée par le maître d'ouvrage",
             31500, "expertise contradictoire", "Cabinet Vaugrenard"),
    Sinistre("SIN-2026-0074", "MRP-2025-0366", "2026-03-29", "2026-03-30",
             "bris de glace",
             "vitrine de la salle côté quai brisée par un projectile pendant la nuit ; "
             "dépôt de plainte effectué au commissariat de La Baule",
             3200, "réglé", None),
    Sinistre("SIN-2026-0081", "DAB-2023-0061", "2026-04-14", "2026-04-15",
             "bris de machine",
             "défaillance du variateur de la cisaille guillotine n° 3 ; "
             "immobilisation de l'atelier de découpe pendant six jours",
             27800, "expertise en cours", "Expertises Tanguy & Fils"),
    Sinistre("SIN-2026-0090", "FLA-2024-0277", "2026-05-06", "2026-05-07",
             "incendie de véhicule",
             "départ de feu dans la trémie de la benne immatriculée GH-905-TR "
             "à la suite du chargement d'un déchet non conforme",
             89000, "expertise en cours", "Expertises Tanguy & Fils"),
    Sinistre("SIN-2026-0103", "MRP-2024-0295", "2026-06-02", "2026-06-02",
             "panne de chambre froide",
             "coupure d'alimentation non détectée sur la chambre froide négative "
             "du point de vente Bugallière ; perte de la totalité des denrées",
             11200, "réglé", None),
    Sinistre("SIN-2026-0112", "RCP-2025-0455", "2026-06-19", "2026-06-24",
             "faute professionnelle alléguée",
             "un client final reproche une régression de facturation introduite lors "
             "d'une montée de version, et réclame la prise en charge de son préjudice",
             58000, "déclaration transmise, position de l'assureur attendue", None),
    Sinistre("SIN-2026-0125", "MRP-2025-0401", "2026-07-08", "2026-07-09",
             "vol par effraction",
             "effraction de la porte de service côté cour, vol de deux ordinateurs "
             "de prépresse et de consommables ; alarme non armée ce soir-là",
             6900, "en cours d'instruction", None),
)

SINISTRES_BY_REF = {sinistre.reference: sinistre for sinistre in SINISTRES}


# --- Référentiel des pièces exigibles -----------------------------------------
#
# **Source unique.** Ce référentiel est rendu en markdown dans le corpus
# (`public/procedures/pieces-par-demande.md`), lu par le traitement des courriels
# depuis l'index, et vérifié par le générateur de boîte : une pièce attendue d'un
# courriel de démonstration DOIT figurer ici, sinon elle serait inatteignable et
# le rappel plafonnerait sans que personne comprenne pourquoi.
#
# La clé porte l'intention, éventuellement suivie d'un sous-cas. C'est elle qui
# permet au traitement d'énumérer les pièces d'une demande plutôt que de les
# inventer : le modèle ne rédige pas une liste, il coche celle-ci.

PIECES_REFERENTIEL: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "new_quote",
        "Nouveau devis entreprise",
        (
            "extrait Kbis de moins de trois mois",
            "dernier bilan clos",
            "relevé de sinistralité des cinq derniers exercices",
            "descriptif des locaux",
            "liste des activités réellement exercées",
        ),
    ),
    (
        "contract_amendment / véhicule",
        "Entrée ou sortie d'un véhicule en flotte",
        (
            "carte grise du véhicule",
            "certificat de cession",
            "date de première mise en circulation",
            "usage du véhicule et nom du conducteur principal",
        ),
    ),
    (
        "contract_amendment / activité",
        "Extension ou modification d'activité",
        (
            "attestation de qualification ou justificatif d'expérience",
            "chiffre d'affaires prévisionnel par activité",
            "description de la technique employée",
        ),
    ),
    (
        "contract_amendment / locaux",
        "Changement d'adresse ou nouvel établissement",
        (
            "adresse complète et surface des nouveaux locaux",
            "nature des locaux et des activités qui y sont exercées",
            "descriptif des protections contre le vol et l'incendie",
        ),
    ),
    (
        "contract_amendment / période",
        "Modification d'une période d'exploitation saisonnière",
        (
            "dates exactes de la période demandée",
            "capacité d'accueil ou surface concernée",
            "mesures de protection appliquées hors saison",
        ),
    ),
    (
        "contract_amendment / capitaux",
        "Modification des capitaux ou des plafonds",
        (
            "valeur actualisée des biens ou des capitaux à garantir",
            "justificatif de la variation demandée",
        ),
    ),
    (
        "claim / dégât des eaux",
        "Déclaration de dégât des eaux",
        (
            "constat amiable dégât des eaux",
            "facture de recherche de fuite",
            "devis de remise en état",
            "photographies des dommages",
            "coordonnées du tiers ou du syndic",
        ),
    ),
    (
        "claim / vol",
        "Déclaration de vol ou de vandalisme",
        (
            "dépôt de plainte",
            "liste chiffrée des biens dérobés avec leurs factures d'achat",
            "attestation de fonctionnement de l'alarme",
            "photographies des points d'effraction",
        ),
    ),
    (
        "claim / incendie",
        "Déclaration d'incendie",
        (
            "rapport d'intervention des pompiers",
            "photographies des dommages",
            "carte grise et dernier contrôle technique du véhicule concerné",
            "inventaire chiffré des biens détruits",
        ),
    ),
    (
        "claim / bris de machine",
        "Déclaration de bris de machine ou de panne d'équipement",
        (
            "rapport du technicien sur la cause de la panne",
            "facture de la pièce ou de la réparation",
            "justificatif de la durée d'immobilisation",
            "contrat d'entretien de la machine",
        ),
    ),
    (
        "claim / cyber",
        "Déclaration d'incident cyber",
        (
            "rapport d'incident du prestataire informatique",
            "journaux techniques des systèmes affectés",
            "date et heure de détection",
            "preuve de la dernière sauvegarde saine",
        ),
    ),
    (
        "claim / mise en cause",
        "Réclamation d'un tiers en responsabilité civile",
        (
            "courrier de réclamation du tiers",
            "contrat ou devis liant l'assuré au réclamant",
            "chiffrage détaillé du préjudice allégué",
            "échanges antérieurs avec le réclamant",
        ),
    ),
    (
        "claim / véhicule confié",
        "Sinistre sur un véhicule confié à l'assuré",
        (
            "dépôt de plainte",
            "ordre de réparation ou preuve de la remise du véhicule",
            "valeur du véhicule et facture d'achat",
            "photographies des points d'effraction",
        ),
    ),
    (
        "certificate_request / tiers",
        "Attestation demandée par un tiers, et non par l'assuré",
        (
            "mandat écrit de l'assuré autorisant la communication",
            "objet précis du marché ou du contrat visé",
        ),
    ),
    (
        "certificate_request / assuré",
        "Attestation demandée par l'assuré lui-même",
        (
            "objet précis du marché ou du chantier visé",
            "établissement ou activité à mentionner",
        ),
    ),
    (
        "billing_or_payment",
        "Cotisation, quittance ou impayé",
        (
            "avis d'échéance ou quittance concernée",
            "preuve du règlement contesté",
            "relevé bancaire faisant apparaître le prélèvement",
        ),
    ),
)

PIECES_PAR_CLE = {cle: pieces for cle, _, pieces in PIECES_REFERENTIEL}

TOUTES_LES_PIECES = frozenset(
    piece for _, _, pieces in PIECES_REFERENTIEL for piece in pieces
)
