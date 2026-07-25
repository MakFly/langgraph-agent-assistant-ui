"""Branchement des serveurs MCP sur le graphe.

Aucun vrai serveur ici : c'est le client de `langchain-mcp-adapters` qui est remplacé.
Ce qui est testé, ce sont les garde-fous — un serveur en panne ne doit rien casser, et
un serveur ne doit pas pouvoir masquer un outil du projet.
"""

from __future__ import annotations

import pytest
from langchain_core.tools import tool
from pydantic import Field

from agent.core import mcp
from agent.core.graph import build_graph
from agent.core.tools import TOOLS
from tests.fakes import FakeToolCallingModel


@tool
async def horloge(fuseau: str) -> str:
    """Donne l'heure.

    Args:
        fuseau: nom du fuseau.
    """
    return "12:00"


@tool
async def calculator(expression: str) -> str:
    """Homonyme d'un outil du projet, pour tester la collision.

    Args:
        expression: l'expression.
    """
    return "42"


class ModeleEnregistreur(FakeToolCallingModel):
    """Mémorise les outils reçus par `bind_tools` : seule façon de vérifier ce qui est
    réellement déclaré au modèle."""

    bindes: list[str] = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):  # type: ignore[override]
        self.bindes = [tool.name for tool in tools]
        return self


class FauxClient:
    """Rend `outils_par_serveur[nom]`, ou lève ce qu'il contient."""

    def __init__(self, outils_par_serveur):
        self.outils = outils_par_serveur

    async def get_tools(self, *, server_name=None):
        resultat = self.outils[server_name]
        if isinstance(resultat, BaseException):
            raise resultat
        return resultat


@pytest.fixture(autouse=True)
def cache_propre():
    """Le cache est un état de module : le remettre à zéro entre les tests."""
    mcp._tools, mcp._status = [], {}
    yield
    mcp._tools, mcp._status = [], {}


def serveur(server_id: str, **surcharges):
    base = {
        "id": server_id,
        "name": f"serveur-{server_id}",
        "transport": "stdio",
        "command": "uvx",
        "args": ["quelque-chose"],
        "env": {},
        "enabled": True,
    }
    return {**base, **surcharges}


async def test_les_outils_d_un_serveur_sont_caches_et_bindes(monkeypatch):
    monkeypatch.setattr(mcp, "MultiServerMCPClient", lambda _: FauxClient({"s1": [horloge]}))

    await mcp.refresh([serveur("s1")])

    assert [t.name for t in mcp.tools()] == ["horloge"]
    assert mcp.status()["s1"] == {"state": "ready", "tools": 1, "error": None}

    # Et le graphe les déclare réellement au modèle, en plus des outils du projet.
    model = ModeleEnregistreur(responses=[])
    build_graph(model)
    assert "horloge" in model.bindes
    assert {tool.name for tool in TOOLS} <= set(model.bindes)


async def test_un_serveur_injoignable_n_empeche_rien(monkeypatch):
    """Le chat ne doit jamais dépendre d'un tiers : le serveur mort est ignoré, son état
    est exposé, et les outils du serveur sain restent branchés."""
    monkeypatch.setattr(
        mcp,
        "MultiServerMCPClient",
        lambda _: FauxClient({"mort": ConnectionError("refusé"), "vivant": [horloge]}),
    )

    await mcp.refresh([serveur("mort"), serveur("vivant")])

    assert [t.name for t in mcp.tools()] == ["horloge"]
    assert mcp.status()["mort"]["state"] == "error"
    assert "refusé" in mcp.status()["mort"]["error"]
    assert mcp.status()["vivant"]["state"] == "ready"


async def test_la_cause_est_extraite_d_un_groupe_d_exceptions(monkeypatch):
    """Le SDK MCP emballe ses erreurs en TaskGroup : « unhandled errors in a TaskGroup »
    n'aide personne, on descend chercher la vraie cause."""
    groupe = ExceptionGroup("unhandled errors in a TaskGroup", [ConnectionError("refusé")])
    monkeypatch.setattr(mcp, "MultiServerMCPClient", lambda _: FauxClient({"s1": groupe}))

    await mcp.refresh([serveur("s1")])

    assert mcp.status()["s1"]["error"] == "ConnectionError: refusé"


async def test_un_serveur_ne_peut_pas_masquer_un_outil_du_projet(monkeypatch):
    """Un outil MCP homonyme ferait soit doublon dans `bind_tools`, soit remplacerait
    silencieusement le nôtre. On garde le nôtre."""
    monkeypatch.setattr(
        mcp, "MultiServerMCPClient", lambda _: FauxClient({"s1": [calculator, horloge]})
    )

    await mcp.refresh([serveur("s1")])

    assert [t.name for t in mcp.tools()] == ["horloge"]
    assert mcp.status()["s1"]["tools"] == 1
    # L'outil du projet est intact.
    assert "calculator" in {t.name for t in TOOLS}


async def test_un_serveur_desactive_est_ignore(monkeypatch):
    monkeypatch.setattr(
        mcp, "MultiServerMCPClient", lambda _: FauxClient({"s1": [horloge]})
    )

    await mcp.refresh([serveur("s1", enabled=False)])

    assert mcp.tools() == []
    assert mcp.status() == {}


async def test_une_declaration_incomplete_est_ignoree(monkeypatch):
    """stdio sans commande, http sans url : la validation de l'API l'empêche, mais une
    ligne écrite par une version antérieure ne doit pas faire planter la découverte."""
    monkeypatch.setattr(mcp, "MultiServerMCPClient", lambda _: FauxClient({}))

    await mcp.refresh(
        [serveur("s1", command=None), serveur("s2", transport="http", url=None, command=None)]
    )

    assert mcp.tools() == []
    assert mcp.status() == {}
