"""Comptes de démonstration : idempotence, garde-fou, cohérence avec le corpus.

Ces comptes ont un mot de passe public. Deux propriétés doivent donc tenir, et
elles sont ici plus importantes que le confort qu'ils apportent :

1. `seed()` refuse de s'exécuter quand l'environnement ressemble à de la
   production ;
2. relancer `seed()` ne réinitialise aucun mot de passe déjà changé.
"""

from __future__ import annotations

import pytest

from agent.core import seed, users
from agent.core.seed import DEMO_ACCOUNTS
from agent.infra import db


@pytest.fixture(autouse=True)
async def database(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("AUTH_COOKIE_SECURE", raising=False)
    monkeypatch.delenv("SEED_PASSWORD", raising=False)

    try:
        await db.connect()
    except Exception as error:  # pragma: no cover
        pytest.skip(f"Postgres injoignable : {error}")

    # Ces tests ont besoin d'une base vierge, mais la base de développement est la
    # même : sans restauration, `make test` supprimait les comptes de démonstration
    # du poste, et le clic sur un compte de l'écran de connexion répondait ensuite
    # « mot de passe incorrect ». Symptôme parfaitement déroutant pour une cause
    # sans aucun rapport. On note donc l'état d'avant, et on le remet après.
    presents = await users.get_by_email(DEMO_ACCOUNTS[0].email) is not None

    await _cleanup()
    users.reset_throttle()
    yield
    await _cleanup()
    if presents:
        await seed.seed(force=True)
    await db.disconnect()


async def _cleanup() -> None:
    await db.pool().execute(
        "DELETE FROM users WHERE email = ANY($1::text[])",
        [account.email for account in seed.DEMO_ACCOUNTS],
    )


async def test_le_seeder_cree_puis_ne_recree_plus():
    first = await seed.seed()
    assert {state for _, state in first} == {"créé"}
    assert len(first) == len(seed.DEMO_ACCOUNTS)

    second = await seed.seed()
    assert {state for _, state in second} == {"existant"}


async def test_relancer_le_seeder_ne_reinitialise_pas_un_mot_de_passe():
    """Le piège classique d'un seeder : il remet tout « comme au début ».

    Ici un compte dont le mot de passe a été changé doit le garder, sinon
    `make seed` rouvrirait silencieusement une porte qu'on venait de fermer.
    """
    await seed.seed()
    compte = await users.get_by_email("finance@demo.local")
    await users.set_password(compte.id, "un-autre-mot-de-passe")

    await seed.seed()

    assert await users.authenticate(compte.email, seed.seed_password()) is None
    assert await users.authenticate(compte.email, "un-autre-mot-de-passe") is not None


async def test_le_seeder_refuse_un_environnement_de_production(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "1")

    with pytest.raises(RuntimeError, match="production"):
        await seed.seed()

    assert await users.get_by_email("finance@demo.local") is None

    # `--force` reste possible, mais il faut le demander explicitement.
    await seed.seed(force=True)
    assert await users.get_by_email("finance@demo.local") is not None


async def test_les_comptes_couvrent_les_groupes_du_corpus():
    """Les groupes des comptes doivent correspondre aux dossiers de `corpus/`.

    Sans ça, la démonstration du filtrage ne montre rien : tous les comptes
    verraient exactement la même chose.
    """
    groupes = {group for account in seed.DEMO_ACCOUNTS for group in account.groups}
    assert groupes == {"finance", "rh"}

    roles = {account.role for account in seed.DEMO_ACCOUNTS}
    assert roles == {"admin", "member"}


async def test_l_administrateur_de_demo_ne_voit_aucun_document_restreint():
    await seed.seed()
    admin = await users.get_by_email("admin@demo.local")
    assert admin.role == "admin"
    # La séparation rôle / groupes vaut aussi pour les comptes de démonstration.
    assert users.effective_groups(admin) == list(users.IMPLICIT_GROUPS)


async def test_les_comptes_de_demo_se_connectent_vraiment():
    await seed.seed()
    for account in seed.DEMO_ACCOUNTS:
        assert await users.authenticate(account.email, seed.seed_password()) is not None
