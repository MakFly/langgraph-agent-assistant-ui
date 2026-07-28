"""Comptes locaux : création, authentification, rôles et groupes.

**Aucun import FastAPI ici.** Ce module est le domaine, comme `agent.core.settings` :
il possède les règles (qu'est-ce qu'un rôle valide, qui voit quoi), pas la
surface HTTP — celle-ci vit dans `agent.api.auth`.

Deux axes d'autorisation, délibérément disjoints :

- **`role`** répond à « qui peut reconfigurer l'agent ? » (prompt système,
  modèle, outils, serveurs MCP, ingestion du corpus). Seul `admin` le peut.
- **`groups`** répond à « quels documents cet utilisateur a-t-il le droit de
  lire ? ». C'est ce qui filtre le RAG. **Être administrateur n'ouvre aucun
  document** : la configuration et la confidentialité ne sont pas le même
  pouvoir, et les confondre est la manière habituelle de fabriquer un compte qui
  voit tout.

La gestion des comptes passe par la CLI (`agent.cli`, cf. `make user-*`) et non
par une API HTTP : un endpoint de création d'utilisateur est une surface d'attaque
qu'un POC n'a aucune raison d'exposer.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from agent.infra import auth, db

logger = logging.getLogger("agent.users")

Role = Literal["admin", "member"]
ROLES: tuple[Role, ...] = ("admin", "member")

# Groupe implicite de tout utilisateur authentifié — l'équivalent du « Everyone »
# d'un annuaire. Il rend `corpus/public/` lisible par tous sans avoir à énumérer
# les comptes, et il est ajouté à la lecture (`effective_groups`) plutôt que
# stocké : changer la politique ne demande alors aucune migration.
IMPLICIT_GROUPS: tuple[str, ...] = ("public",)

# Verrouillage anti-force brute. En mémoire, donc remis à zéro au redémarrage et
# inopérant en multi-instance : c'est un ralentisseur, pas un rempart. Le vrai
# rempart reste le coût d'Argon2 par tentative.
_MAX_FAILURES = 10
_FAILURE_WINDOW = timedelta(minutes=15)
_failures: dict[str, list[datetime]] = {}


class TooManyAttempts(Exception):
    """Trop d'échecs récents sur cet e-mail."""


class User(BaseModel):
    id: str
    email: str
    display_name: str | None = None
    role: Role = "member"
    groups: list[str] = Field(default_factory=list)
    disabled: bool = False


def normalize_email(email: str) -> str:
    return email.strip().lower()


def effective_groups(user: User) -> list[str]:
    """Groupes réellement opposables au filtre ACL du RAG.

    Trié pour que deux appels donnent la même liste : c'est ce qui rend les
    requêtes SQL et les tests déterministes.
    """
    return sorted({*user.groups, *IMPLICIT_GROUPS})


def _to_user(row) -> User:
    return User(
        id=row["id"],
        email=row["email"],
        display_name=row["display_name"],
        role=row["role"] if row["role"] in ROLES else "member",
        groups=list(row["groups"] or []),
        disabled=row["disabled"],
    )


# --- Lecture ------------------------------------------------------------------


async def count_users() -> int:
    return await db.pool().fetchval("SELECT count(*) FROM users")


async def get_user(user_id: str) -> User | None:
    row = await db.pool().fetchrow(
        "SELECT id, email, display_name, role, groups, disabled FROM users WHERE id = $1",
        user_id,
    )
    return _to_user(row) if row else None


async def get_by_email(email: str) -> User | None:
    row = await db.pool().fetchrow(
        "SELECT id, email, display_name, role, groups, disabled FROM users WHERE email = $1",
        normalize_email(email),
    )
    return _to_user(row) if row else None


async def list_users() -> list[User]:
    rows = await db.pool().fetch(
        """
        SELECT id, email, display_name, role, groups, disabled
        FROM users ORDER BY email
        """
    )
    return [_to_user(row) for row in rows]


# --- Écriture -----------------------------------------------------------------


async def create_user(
    email: str,
    password: str,
    *,
    role: Role = "member",
    groups: list[str] | None = None,
    display_name: str | None = None,
) -> User:
    """Crée un compte. Lève `ValueError` si l'e-mail est déjà pris.

    Le hachage a lieu ici et jamais dans la couche HTTP : c'est la garantie qu'un
    mot de passe en clair ne peut pas atteindre la base par un chemin oublié.
    """
    email = normalize_email(email)
    if not email or "@" not in email:
        raise ValueError("E-mail invalide")
    if len(password) < 12:
        # 12 caractères : le plancher NIST pour un secret choisi par un humain.
        raise ValueError("Mot de passe trop court (12 caractères minimum)")
    if role not in ROLES:
        raise ValueError(f"Rôle inconnu : {role}")

    row = await db.pool().fetchrow(
        """
        INSERT INTO users (id, email, password_hash, display_name, role, groups)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (email) DO NOTHING
        RETURNING id, email, display_name, role, groups, disabled
        """,
        uuid.uuid4().hex,
        email,
        auth.hash_password(password),
        display_name,
        role,
        groups or [],
    )
    if row is None:
        raise ValueError(f"Un compte existe déjà pour {email}")

    logger.info("compte créé", extra={"role": role, "groupes": len(groups or [])})
    return _to_user(row)


async def set_password(user_id: str, password: str) -> bool:
    if len(password) < 12:
        raise ValueError("Mot de passe trop court (12 caractères minimum)")
    result = await db.pool().execute(
        "UPDATE users SET password_hash = $2, updated_at = now() WHERE id = $1",
        user_id,
        auth.hash_password(password),
    )
    return not result.endswith("0")


async def set_groups(user_id: str, groups: list[str]) -> bool:
    result = await db.pool().execute(
        "UPDATE users SET groups = $2, updated_at = now() WHERE id = $1",
        user_id,
        sorted({group.strip() for group in groups if group.strip()}),
    )
    return not result.endswith("0")


async def set_role(user_id: str, role: Role) -> bool:
    if role not in ROLES:
        raise ValueError(f"Rôle inconnu : {role}")
    result = await db.pool().execute(
        "UPDATE users SET role = $2, updated_at = now() WHERE id = $1", user_id, role
    )
    return not result.endswith("0")


async def set_disabled(user_id: str, disabled: bool) -> bool:
    """Désactive un compte.

    Rappel : le jeton d'ACCÈS déjà émis reste valide jusqu'à sa courte expiration
    (`AUTH_ACCESS_TTL_MINUTES`) — il se vérifie sans base et ne se révoque donc pas
    à chaud. Le refresh, lui, recharge le compte et refuse un `disabled` : l'accès
    est coupé au plus tard à cette expiration. Pour couper l'instant même, faire
    aussi tourner `AUTH_SECRET`, ce qui déconnecte tout le monde. La révocation des
    sessions de refresh se fait à part (`agent.core.sessions.revoke_all_for_user`,
    appelé par la CLI de désactivation).
    """
    result = await db.pool().execute(
        "UPDATE users SET disabled = $2, updated_at = now() WHERE id = $1", user_id, disabled
    )
    return not result.endswith("0")


async def delete_user(user_id: str) -> bool:
    result = await db.pool().execute("DELETE FROM users WHERE id = $1", user_id)
    return not result.endswith("0")


# --- Authentification ---------------------------------------------------------


def _throttled(email: str) -> bool:
    recent = [
        moment
        for moment in _failures.get(email, [])
        if datetime.now(UTC) - moment < _FAILURE_WINDOW
    ]
    _failures[email] = recent
    return len(recent) >= _MAX_FAILURES


def _record_failure(email: str) -> None:
    _failures.setdefault(email, []).append(datetime.now(UTC))


def reset_throttle() -> None:
    """Vide le compteur d'échecs — utilisé par les tests."""
    _failures.clear()


async def authenticate(email: str, password: str) -> User | None:
    """Le compte, ou None si l'e-mail ou le mot de passe est faux.

    La distinction « e-mail inconnu » / « mot de passe faux » n'est jamais rendue
    à l'appelant, ni en valeur de retour ni en durée : `dummy_verify()` égalise
    le temps de réponse pour que le chronomètre ne trahisse pas l'existence d'un
    compte.

    Raises:
        TooManyAttempts: trop d'échecs récents sur cet e-mail.
    """
    email = normalize_email(email)

    if _throttled(email):
        logger.warning("connexion bloquée : trop de tentatives")
        raise TooManyAttempts

    row = await db.pool().fetchrow(
        """
        SELECT id, email, display_name, role, groups, disabled, password_hash
        FROM users WHERE email = $1
        """,
        email,
    )

    if row is None:
        auth.dummy_verify()
        _record_failure(email)
        return None

    if not auth.verify_password(row["password_hash"], password):
        _record_failure(email)
        return None

    if row["disabled"]:
        # Compté comme un échec : sinon l'endpoint devient un oracle qui dit
        # « ce mot de passe est bon, mais le compte est fermé ».
        _record_failure(email)
        return None

    # Les paramètres d'Argon2 évoluent avec les versions de la bibliothèque : on
    # remet à niveau au moment où le mot de passe en clair est disponible, seul
    # instant où c'est possible.
    if auth.needs_rehash(row["password_hash"]):
        await db.pool().execute(
            "UPDATE users SET password_hash = $2 WHERE id = $1",
            row["id"],
            auth.hash_password(password),
        )
        logger.info("haché de mot de passe remis à niveau")

    _failures.pop(email, None)
    return _to_user(row)


# --- Amorçage -----------------------------------------------------------------


async def bootstrap_admin() -> User | None:
    """Crée le premier administrateur depuis l'environnement, si besoin.

    Ne fait rien s'il existe déjà au moins un compte : cette fonction tourne à
    chaque démarrage et ne doit jamais recréer ni réinitialiser quoi que ce soit.

    Aucun mot de passe n'est généré ni journalisé — sans `ADMIN_EMAIL` et
    `ADMIN_PASSWORD`, on se contente de dire quoi faire.
    """
    if await count_users() > 0:
        return None

    email = os.getenv("ADMIN_EMAIL", "").strip()
    password = os.getenv("ADMIN_PASSWORD", "")

    if not email or not password:
        logger.warning(
            "aucun compte en base et ADMIN_EMAIL/ADMIN_PASSWORD non définis : "
            "personne ne peut se connecter. Créez un compte avec `make user-create`."
        )
        return None

    try:
        user = await create_user(
            email, password, role="admin", groups=[], display_name="Administrateur"
        )
    except ValueError as error:
        logger.error("amorçage de l'administrateur impossible : %s", error)
        return None

    logger.info("administrateur initial créé depuis l'environnement")
    return user
