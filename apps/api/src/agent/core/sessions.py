"""Sessions de rafraîchissement : la couche RÉVOCABLE au-dessus du JWT d'accès.

**Aucun import FastAPI ici.** Comme `agent.core.users`, ce module est du domaine :
il possède les règles de vie d'une session (rotation, détection de rejeu,
révocation), pas la surface HTTP — celle-ci vit dans `agent.api.auth`.

Le jeton d'accès (`agent.infra.auth`) reste auto-porteur et se vérifie sans SQL :
cette table n'intervient QUE lors d'un login, d'un refresh ou d'un logout. C'est
ce qui préserve la propriété du POC — le chat survit à une panne Postgres jusqu'à
l'expiration du jeton d'accès (court).

Modèle : **une ligne par session** (un appareil). À chaque refresh on ROTATE — un
nouveau jeton est émis, le haché de l'ancien passe en `previous_hash`. Présenter un
jeton déjà pivoté (celui de `previous_hash`) ne peut venir que d'un rejeu : on
considère le compte compromis et on révoque **toutes** ses sessions. La contrepartie
assumée de ce schéma à une génération : seul le dernier jeton pivoté est détectable
en rejeu — au-delà, le jeton est simplement inconnu (donc refusé, sans alerte).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from agent.infra import auth, db

logger = logging.getLogger("agent.sessions")


class ReuseDetected(Exception):
    """Un jeton de refresh déjà pivoté a été rejoué : vol probable."""


@dataclass(frozen=True)
class IssuedRefresh:
    """Un jeton de refresh fraîchement émis. `token` est en CLAIR, à poser en
    cookie et à ne jamais journaliser ni restocker : la base n'en a que l'empreinte."""

    session_id: str
    token: str


async def create(user_id: str, *, user_agent: str | None = None) -> IssuedRefresh:
    """Ouvre une session pour `user_id` et rend son premier jeton de refresh."""
    pool = db.pool()

    # Élagage opportuniste : on ne laisse pas les sessions expirées s'accumuler
    # indéfiniment (une par connexion, sur des années). Fait au moment d'en créer
    # une, donc sans tâche de fond à maintenir.
    await pool.execute(
        "DELETE FROM sessions WHERE user_id = $1 AND expires_at < now()", user_id
    )

    token = auth.new_refresh_token()
    session_id = uuid.uuid4().hex
    expires_at = datetime.now(UTC) + auth.refresh_ttl()
    await pool.execute(
        """
        INSERT INTO sessions (id, user_id, token_hash, expires_at, user_agent)
        VALUES ($1, $2, $3, $4, $5)
        """,
        session_id,
        user_id,
        auth.hash_refresh_token(token),
        expires_at,
        user_agent,
    )
    return IssuedRefresh(session_id, token)


async def rotate(token: str) -> tuple[str, IssuedRefresh]:
    """Vérifie un jeton de refresh et le fait tourner.

    Rend `(user_id, nouveau_refresh)` sur le chemin nominal.

    Raises:
        ReuseDetected: le jeton présenté est un jeton DÉJÀ pivoté (rejeu). Toutes
            les sessions du compte concerné ont alors été révoquées.
        LookupError: le jeton est inconnu, expiré ou révoqué.
    """
    pool = db.pool()
    token_hash = auth.hash_refresh_token(token)

    # Chemin nominal : le jeton EST le jeton courant d'une session vivante.
    row = await pool.fetchrow(
        """
        SELECT id, user_id FROM sessions
        WHERE token_hash = $1 AND revoked_at IS NULL AND expires_at > now()
        """,
        token_hash,
    )
    if row is not None:
        new_token = auth.new_refresh_token()
        new_expires = datetime.now(UTC) + auth.refresh_ttl()
        await pool.execute(
            """
            UPDATE sessions
            SET previous_hash = token_hash,
                token_hash    = $2,
                rotated_at    = now(),
                expires_at    = $3
            WHERE id = $1
            """,
            row["id"],
            auth.hash_refresh_token(new_token),
            new_expires,
        )
        return row["user_id"], IssuedRefresh(row["id"], new_token)

    # Détection de rejeu : ce jeton était le jeton PRÉCÉDENT d'une session — donc
    # déjà pivoté. Le revoir signale que deux porteurs se sont partagé le même
    # jeton (vol) : on coupe tout le compte, conformément à la politique retenue.
    reused = await pool.fetchrow(
        "SELECT user_id FROM sessions WHERE previous_hash = $1", token_hash
    )
    if reused is not None:
        await revoke_all_for_user(reused["user_id"])
        logger.warning("réutilisation d'un refresh token détectée : compte révoqué")
        raise ReuseDetected

    raise LookupError("jeton de refresh inconnu, expiré ou révoqué")


async def revoke_by_token(token: str) -> None:
    """Révoque la session qui porte ce jeton (courant ou fraîchement pivoté).

    Utilisé au logout : ne lève pas si rien ne correspond — se déconnecter deux
    fois n'est pas une erreur."""
    token_hash = auth.hash_refresh_token(token)
    await db.pool().execute(
        """
        UPDATE sessions SET revoked_at = now()
        WHERE (token_hash = $1 OR previous_hash = $1) AND revoked_at IS NULL
        """,
        token_hash,
    )


async def revoke_all_for_user(user_id: str) -> None:
    """Coupe toutes les sessions d'un compte : rejeu détecté, ou désactivation."""
    await db.pool().execute(
        "UPDATE sessions SET revoked_at = now() WHERE user_id = $1 AND revoked_at IS NULL",
        user_id,
    )
