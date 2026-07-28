"""Primitives d'authentification : hachage de mot de passe et jetons signés.

**Aucune notion d'utilisateur ici, aucune I/O base, aucun import FastAPI.** Ce
module ne contient que de la technique cryptographique. Le domaine (qui est
l'utilisateur, quels groupes) vit dans `agent.core.users` ; la surface HTTP dans
`agent.api.auth`.

Trois choix qui méritent d'être écrits, parce qu'ils se paient plus tard :

1. **Argon2id** plutôt que bcrypt. C'est la recommandation OWASP courante, et
   `argon2-cffi` gère seul le sel et les paramètres — il n'y a rien à régler,
   donc rien à mal régler.

2. **Le jeton d'accès est auto-porteur** : il transporte `sub`, `role` et
   `groups`. Vérifier une requête ne coûte donc aucune requête SQL, et le chat
   continue de fonctionner quand Postgres tombe — la propriété que le POC avait
   déjà (`agent.infra.db.is_available`) et qu'une auth naïve aurait détruite.
   Il est désormais **court** (`AUTH_ACCESS_TTL_MINUTES`, 15 min par défaut) et
   doublé d'un **jeton de refresh révocable** stocké en base (`agent.core.sessions`).
   Conséquence : désactiver un compte (ou révoquer une session) prend effet au
   prochain refresh, soit au pire après la durée du jeton d'accès — 15 min, contre
   les 8 h du modèle purement auto-porteur. La survie à une panne Postgres tombe
   du même coup à cette durée : c'est l'arbitrage explicite du refresh.

3. **Le secret n'est pas obligatoire au démarrage.** Sans `AUTH_SECRET`, un
   secret aléatoire est généré pour la durée du processus : le serveur démarre
   quand même (contrainte du POC), mais les sessions ne survivent pas à un
   redémarrage et un WARNING le dit.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

logger = logging.getLogger("agent.auth")

ALGORITHM = "HS256"
ISSUER = "langgraph-poc"

# Le jeton voyage dans un cookie httpOnly : inaccessible au JavaScript, donc non
# exfiltrable par une XSS — contrairement à localStorage. `SameSite=Lax` couvre
# le CSRF pour les requêtes cross-site non navigationnelles.
COOKIE_NAME = "agent_session"

# Cookie du jeton de refresh. Portée réduite à `/api/auth` : le navigateur ne
# l'envoie donc qu'aux endpoints d'authentification, jamais à `/api/chat` ni au
# reste — un cookie qui ne circule pas est un cookie qu'on n'expose pas.
REFRESH_COOKIE_NAME = "agent_refresh"
REFRESH_COOKIE_PATH = "/api/auth"

# Longueur en dessous de laquelle un secret fourni ne vaut pas mieux que pas de
# secret du tout. 32 octets, c'est la taille de la sortie de HMAC-SHA256.
_MIN_SECRET_LENGTH = 32

_hasher = PasswordHasher()

# Haché d'une valeur jetable, utilisé pour égaliser le temps de réponse quand
# l'e-mail est inconnu (cf. `dummy_verify`).
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(32))

_generated_secret: str | None = None


def _secret() -> str:
    """Secret de signature, résolu paresseusement.

    Lu à chaque appel plutôt qu'à l'import : les tests peuvent poser
    `AUTH_SECRET` après le chargement du module, et un secret capturé à l'import
    serait faux pour toute la suite.
    """
    global _generated_secret

    configured = os.getenv("AUTH_SECRET", "").strip()
    if configured:
        if len(configured) < _MIN_SECRET_LENGTH:
            logger.warning(
                "AUTH_SECRET trop court, les jetons sont faiblement signés",
                extra={"longueur": len(configured), "minimum": _MIN_SECRET_LENGTH},
            )
        return configured

    if _generated_secret is None:
        _generated_secret = secrets.token_urlsafe(48)
        logger.warning(
            "AUTH_SECRET absent : secret éphémère généré. Les sessions seront "
            "invalidées au prochain redémarrage — définissez AUTH_SECRET."
        )
    return _generated_secret


def access_ttl() -> timedelta:
    """Durée de vie du jeton d'ACCÈS. C'est aussi le délai maximal avant qu'une
    révocation (compte désactivé, session coupée) devienne effective : c'est le
    refresh qui la constate, et il n'a lieu qu'à l'expiration de l'accès."""
    try:
        minutes = float(os.getenv("AUTH_ACCESS_TTL_MINUTES", "15"))
    except ValueError:
        logger.warning("AUTH_ACCESS_TTL_MINUTES illisible, repli sur 15 min")
        minutes = 15.0
    # Bornes : sous la minute le jeton expire pendant la requête ; au-delà d'un jour
    # on retombe dans les travers du jeton long — révocation lente, panne longue.
    minutes = max(1.0, min(minutes, 24 * 60))
    return timedelta(minutes=minutes)


def refresh_ttl() -> timedelta:
    """Durée de vie d'un jeton de REFRESH : la borne dure d'une session inactive."""
    try:
        days = float(os.getenv("AUTH_REFRESH_TTL_DAYS", "14"))
    except ValueError:
        logger.warning("AUTH_REFRESH_TTL_DAYS illisible, repli sur 14 j")
        days = 14.0
    # Bornes : au moins une heure (sinon le refresh ne sert à rien), au plus un an.
    days = max(1 / 24, min(days, 365))
    return timedelta(days=days)


# --- Mots de passe ------------------------------------------------------------


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(stored_hash: str, plain: str) -> bool:
    """Vrai si le mot de passe correspond. Ne lève jamais.

    Toutes les exceptions d'argon2 signifient la même chose du point de vue de
    l'appelant — « ce n'est pas le bon mot de passe » — et les distinguer dans la
    réponse HTTP renseignerait un attaquant sur l'état du compte.
    """
    try:
        return _hasher.verify(stored_hash, plain)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    """Les paramètres d'argon2 ont-ils évolué depuis ce haché ?"""
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return False


def dummy_verify() -> None:
    """Brûle le temps d'une vérification pour un e-mail inconnu.

    Sans ça, une réponse instantanée signale « ce compte n'existe pas » et permet
    d'énumérer les utilisateurs par simple chronométrage.
    """
    with contextlib.suppress(VerifyMismatchError, VerificationError, InvalidHashError):
        _hasher.verify(_DUMMY_HASH, "mot-de-passe-invalide")


# --- Jetons -------------------------------------------------------------------


def encode_token(claims: dict[str, Any]) -> str:
    """Signe un jeton en y ajoutant `iss`, `iat` et `exp`."""
    now = datetime.now(UTC)
    payload = {
        **claims,
        "iss": ISSUER,
        "iat": now,
        "exp": now + access_ttl(),
    }
    return jwt.encode(payload, _secret(), algorithm=ALGORITHM)


def decode_token(token: str) -> dict[str, Any] | None:
    """Claims du jeton, ou None s'il est invalide, expiré ou mal émis.

    `algorithms` est une liste fermée : sans elle, un jeton forgé avec
    `"alg": "none"` serait accepté. C'est *la* faille historique des
    implémentations JWT.
    """
    try:
        return jwt.decode(
            token,
            _secret(),
            algorithms=[ALGORITHM],
            issuer=ISSUER,
            options={"require": ["exp", "iat", "iss", "sub"]},
        )
    except jwt.PyJWTError as error:
        logger.debug("jeton rejeté : %s", error)
        return None


# --- Jetons de refresh --------------------------------------------------------


def new_refresh_token() -> str:
    """Un jeton de refresh : 256 bits d'aléa, opaque. Rendu au client en clair une
    seule fois (posé en cookie), jamais restocké tel quel — seule son empreinte
    (`hash_refresh_token`) va en base."""
    return secrets.token_urlsafe(32)


def hash_refresh_token(token: str) -> str:
    """Empreinte stockée en base. sha256 suffit : le jeton est DÉJÀ 256 bits
    d'aléa, donc hors de portée d'un dictionnaire. Argon2 (lent, anti-force-brute)
    ne protège que les secrets à faible entropie comme les mots de passe ; son coût
    sur un chemin appelé à chaque refresh serait payé pour rien."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
