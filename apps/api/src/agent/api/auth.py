"""Connexion, déconnexion, et les dépendances d'autorisation du reste de l'API.

Ce module est **le seul endroit où une identité entre dans l'application**. Tout
le reste du code reçoit un `User` déjà vérifié en paramètre ; aucun autre module
ne lit de cookie ni d'en-tête. C'est ce qui rend la question « peut-on accéder à
cette route sans jeton ? » vérifiable en lisant une seule page.

Le jeton part dans un cookie `httpOnly`. Deux conséquences agréables :

- le JavaScript du front ne peut pas le lire, donc une XSS ne l'exfiltre pas ;
- le navigateur le renvoie tout seul, donc `assistant-ui` appelle `/api/chat`
  sans qu'on ait à câbler un en-tête dans son transport.

L'en-tête `Authorization: Bearer` reste accepté, pour `curl` et les tests.
"""

from __future__ import annotations

import contextlib
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from agent.core import sessions, users
from agent.core.users import User
from agent.infra import auth

logger = logging.getLogger("agent.api.auth")

router = APIRouter(prefix="/api/auth", tags=["auth"])

_UNAUTHORIZED = HTTPException(
    status_code=401,
    detail="Authentification requise",
    headers={"WWW-Authenticate": "Bearer"},
)


class LoginRequest(BaseModel):
    email: str
    password: str


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


def _user_from_claims(claims: dict) -> User | None:
    """Reconstruit l'utilisateur depuis le jeton pour le repli sans base.

    En fonctionnement nominal, `optional_user()` recharge le compte : un JWT
    valide dont le sujet a été supprimé ne doit pas survivre jusqu'à une
    violation de clé étrangère. Ce repli préserve seulement le mode dégradé
    historique quand Postgres est momentanément indisponible.
    """
    subject = claims.get("sub")
    if not subject:
        return None

    groups = claims.get("groups")
    return User(
        id=subject,
        email=claims.get("email", ""),
        display_name=claims.get("name"),
        role=claims.get("role") if claims.get("role") in users.ROLES else "member",
        groups=list(groups) if isinstance(groups, list) else [],
    )


async def optional_user(request: Request) -> User | None:
    """L'utilisateur s'il est authentifié, None sinon. Ne lève jamais."""
    token = _bearer(request) or request.cookies.get(auth.COOKIE_NAME)
    if not token:
        return None
    claims = auth.decode_token(token)
    claimed = _user_from_claims(claims) if claims else None
    if claimed is None:
        return None

    try:
        current = await users.get_user(claimed.id)
    except Exception as error:  # noqa: BLE001 - dépendance « ne lève jamais »
        # Disponibilité assumée : un incident Postgres ne coupe pas un chat déjà
        # authentifié. Les ACL restent celles signées dans le jeton et le RAG a
        # sa propre base. Dès que Postgres revient, le compte est revérifié.
        logger.warning("validation du compte impossible, repli sur le JWT : %s", error)
        return claimed

    # Compte supprimé/recréé ou désactivé : le JWT signé n'autorise plus rien.
    return None if current is None or current.disabled else current


async def current_user(request: Request) -> User:
    """L'utilisateur authentifié, ou 401.

    C'est la dépendance à poser sur toute route qui touche à des données.
    """
    user = await optional_user(request)
    if user is None:
        raise _UNAUTHORIZED
    return user


async def require_admin(user: User = Depends(current_user)) -> User:
    """Réservé au rôle `admin` : reconfiguration de l'agent, ingestion.

    Ne donne accès à **aucun document** : la lecture du corpus dépend des
    groupes, pas du rôle (cf. `agent.core.users`).
    """
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Réservé aux administrateurs")
    return user


def _cookie_secure() -> bool:
    """`Secure` désactivable : en dev, le front est servi en HTTP sur localhost
    et un cookie `Secure` n'y serait jamais renvoyé."""
    return os.getenv("AUTH_COOKIE_SECURE", "0").strip().lower() in {"1", "true", "yes"}


def _issue_access(response: Response, user: User) -> None:
    """Signe un jeton d'accès pour `user` et le pose en cookie.

    Le jeton reste auto-porteur pour le mode dégradé, mais les requêtes nominales
    rechargent le compte : suppression, désactivation et changements de droits
    prennent donc effet sans attendre son expiration."""
    token = auth.encode_token(
        {
            "sub": user.id,
            "email": user.email,
            "name": user.display_name,
            "role": user.role,
            "groups": users.effective_groups(user),
        }
    )
    response.set_cookie(
        auth.COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(),
        max_age=int(auth.access_ttl().total_seconds()),
        path="/",
    )


def _set_refresh_cookie(response: Response, token: str) -> None:
    """Pose le cookie de refresh, scopé `/api/auth` : le navigateur ne l'envoie
    qu'aux endpoints d'authentification, jamais à `/api/chat`."""
    response.set_cookie(
        auth.REFRESH_COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(),
        max_age=int(auth.refresh_ttl().total_seconds()),
        path=auth.REFRESH_COOKIE_PATH,
    )


def _clear_auth_cookies(response: Response) -> None:
    """Efface les deux cookies, chacun avec le `path` qui a servi à le poser —
    un `delete_cookie` de path différent ne toucherait pas le bon cookie."""
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    response.delete_cookie(auth.REFRESH_COOKIE_NAME, path=auth.REFRESH_COOKIE_PATH)


def _deny_refresh() -> JSONResponse:
    """401 pour un refresh refusé, cookies effacés au passage.

    On **retourne** la réponse au lieu de `raise` : Starlette construit sa propre
    réponse pour une `HTTPException` et y perdrait les en-têtes `Set-Cookie` posés
    sur la réponse injectée. Seule une réponse *retournée* emporte la suppression."""
    denied = JSONResponse(
        status_code=401,
        content={"detail": "Authentification requise"},
        headers={"WWW-Authenticate": "Bearer"},
    )
    _clear_auth_cookies(denied)
    return denied


@router.post("/login")
async def login(payload: LoginRequest, request: Request, response: Response) -> dict:
    try:
        user = await users.authenticate(payload.email, payload.password)
    except users.TooManyAttempts:
        raise HTTPException(
            status_code=429, detail="Trop de tentatives, réessayez dans quelques minutes"
        ) from None
    except RuntimeError:
        # `db.pool()` sans pool : la base est absente. La connexion est le seul
        # chemin qui en a besoin — un utilisateur déjà connecté continue, lui,
        # de discuter (le jeton se vérifie sans SQL).
        raise HTTPException(
            status_code=503, detail="Base indisponible : connexion impossible"
        ) from None

    if user is None:
        # Message unique et volontairement vague : ne pas dire lequel des deux
        # champs est faux.
        raise HTTPException(status_code=401, detail="E-mail ou mot de passe incorrect")

    _issue_access(response, user)
    issued = await sessions.create(user.id, user_agent=request.headers.get("user-agent"))
    _set_refresh_cookie(response, issued.token)
    logger.info("connexion réussie", extra={"role": user.role})
    return {"user": _public(user)}


@router.post("/refresh")
async def refresh(request: Request, response: Response) -> dict:
    """Renouvelle le jeton d'accès à partir du cookie de refresh, et fait tourner
    ce dernier. C'est le seul endroit où une session révoquée ou un compte
    désactivé sont réellement constatés — au plus tard `access_ttl` après le fait.

    Toute issue non nominale renvoie un 401 qui efface les deux cookies : le front
    repart alors sur l'écran de connexion plutôt que de boucler sur un refresh voué
    à l'échec. Seul un 503 (base indisponible) laisse la session intacte."""
    token = request.cookies.get(auth.REFRESH_COOKIE_NAME)
    if not token:
        return _deny_refresh()

    try:
        user_id, issued = await sessions.rotate(token)
    except (sessions.ReuseDetected, LookupError):
        return _deny_refresh()
    except RuntimeError:
        # Pool absent : la base est indisponible. Le refresh en dépend par nature
        # (c'est l'arbitrage du modèle) — on le dit, sans détruire la session.
        raise HTTPException(
            status_code=503, detail="Base indisponible : session non renouvelée"
        ) from None

    # Rechargé depuis la base : un compte désactivé ou aux droits changés depuis
    # l'émission est pris en compte ici, et nulle part ailleurs.
    user = await users.get_user(user_id)
    if user is None or user.disabled:
        await sessions.revoke_all_for_user(user_id)
        return _deny_refresh()

    _issue_access(response, user)
    _set_refresh_cookie(response, issued.token)
    return {"user": _public(user)}


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict:
    # Révocation côté serveur : effacer le cookie ne suffit pas, un jeton de
    # refresh copié ailleurs resterait sinon utilisable jusqu'à son expiration.
    token = request.cookies.get(auth.REFRESH_COOKIE_NAME)
    if token:
        with contextlib.suppress(Exception):
            await sessions.revoke_by_token(token)
    _clear_auth_cookies(response)
    return {"status": "ok"}


@router.get("/me")
async def me(user: User = Depends(current_user)) -> dict:
    return {"user": _public(user)}


def _public(user: User) -> dict:
    """Représentation publique d'un compte : jamais de haché de mot de passe."""
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
        "groups": users.effective_groups(user),
    }
