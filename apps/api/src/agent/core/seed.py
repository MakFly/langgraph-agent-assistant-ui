"""Comptes de démonstration, alignés sur le corpus de `corpus/`.

**Ces comptes ont un mot de passe connu et écrit dans le dépôt.** C'est exactement
ce qu'il faut pour montrer le filtrage par groupes en trente secondes, et
exactement ce qu'il ne faut jamais approcher d'un déploiement réel. D'où le
garde-fou de `seed()` : la commande refuse de s'exécuter quand
`AUTH_COOKIE_SECURE` est actif, seul indicateur de production dont dispose ce
projet.

L'intérêt des quatre comptes est de rendre les ACL *visibles* : la même question
sur le budget donne une réponse à `finance@demo.local` et rien à
`rh@demo.local`. Sans plusieurs comptes, le filtrage est une affirmation ; avec,
c'est une démonstration.

Le seeder est **idempotent** : un compte déjà présent n'est ni recréé, ni
réinitialisé. Relancer `make seed` ne remet donc jamais un mot de passe modifié
à sa valeur d'usine.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from agent.core import users

logger = logging.getLogger("agent.seed")

# Mot de passe des comptes de démonstration. Surchargeable, mais le front ne
# connaît que cette valeur par défaut : la changer désactive de fait l'autofill
# de l'écran de connexion (cf. apps/web/src/components/auth/demo-accounts.ts).
DEFAULT_SEED_PASSWORD = "demo-motdepasse-1"


@dataclass(frozen=True)
class DemoAccount:
    email: str
    display_name: str
    role: users.Role
    groups: tuple[str, ...]
    #: Ce que ce compte est censé démontrer, affiché par la CLI et le front.
    purpose: str


# Les groupes correspondent aux dossiers de `corpus/`. Un compte sans groupe
# explicite garde le groupe implicite `public` (cf. users.IMPLICIT_GROUPS).
DEMO_ACCOUNTS: tuple[DemoAccount, ...] = (
    DemoAccount(
        email="admin@demo.local",
        display_name="Admin démo",
        role="admin",
        groups=(),
        purpose="reconfigure l'agent, mais ne voit AUCUN document restreint",
    ),
    DemoAccount(
        email="finance@demo.local",
        display_name="Camille (finance)",
        role="member",
        groups=("finance",),
        purpose="voit le budget, pas les documents RH",
    ),
    DemoAccount(
        email="rh@demo.local",
        display_name="Dominique (RH)",
        role="member",
        groups=("rh",),
        purpose="voit les congés, pas le budget",
    ),
    DemoAccount(
        email="public@demo.local",
        display_name="Alex (sans groupe)",
        role="member",
        groups=(),
        purpose="ne voit que les documents publics",
    ),
)


def seed_password() -> str:
    return os.getenv("SEED_PASSWORD", "").strip() or DEFAULT_SEED_PASSWORD


def production_guard_active() -> bool:
    """Y a-t-il un signe qu'on ne soit pas en développement ?

    `AUTH_COOKIE_SECURE` est le seul drapeau du projet qui distingue les deux
    mondes : il impose HTTPS pour le cookie de session, ce qui n'a de sens qu'en
    déploiement réel.
    """
    return os.getenv("AUTH_COOKIE_SECURE", "0").strip().lower() in {"1", "true", "yes"}


async def seed(*, force: bool = False) -> list[tuple[DemoAccount, str]]:
    """Crée les comptes de démonstration manquants.

    Returns:
        La liste `(compte, état)` où l'état vaut `créé` ou `existant`.

    Raises:
        RuntimeError: garde-fou de production actif et `force` non demandé.
    """
    if production_guard_active() and not force:
        raise RuntimeError(
            "AUTH_COOKIE_SECURE est actif : cet environnement ressemble à de la "
            "production, et ces comptes ont un mot de passe public. Refus. "
            "Utilisez --force si vous savez vraiment ce que vous faites."
        )

    password = seed_password()
    outcome: list[tuple[DemoAccount, str]] = []

    for account in DEMO_ACCOUNTS:
        if await users.get_by_email(account.email) is not None:
            outcome.append((account, "existant"))
            continue

        await users.create_user(
            account.email,
            password,
            role=account.role,
            groups=list(account.groups),
            display_name=account.display_name,
        )
        outcome.append((account, "créé"))

    created = sum(1 for _, state in outcome if state == "créé")
    logger.info("comptes de démonstration", extra={"créés": created})
    return outcome
