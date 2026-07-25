from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, trim_messages
from langchain_core.messages.utils import count_tokens_approximately
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import RetryPolicy

from agent.core import mcp, settings
from agent.core.model import create_model
from agent.core.tools import TOOLS, tool_error_message

# Garde-fou : sans plafond, un modèle qui boucle sur un outil en erreur brûle le
# quota gratuit en quelques secondes. C'est le premier truc qui casse en prod.
# La valeur est configurable (1..20) via /api/settings/agent ; celle-ci est le défaut.
MAX_TOOL_LOOPS = settings.DEFAULT_MAX_TOOL_LOOPS

logger = logging.getLogger("agent.graph")

# Fenêtre de contexte appliquée à l'historique. Définie dans `agent.settings` (c'est une
# limite de configuration, pas une propriété du graphe) et exposée au front pour qu'il
# affiche le contexte restant avec exactement le même plafond.
MAX_CONTEXT_TOKENS = settings.MAX_CONTEXT_TOKENS

# Reprise sur erreur transitoire du provider. Le mode de panne numéro un d'un POC sur
# free tier, c'est le 429 : sans ça, il tue le run et l'utilisateur retape son message.
AGENT_RETRY = RetryPolicy(
    max_attempts=3,
    initial_interval=0.5,
    backoff_factor=2.0,
    max_interval=8.0,
    jitter=True,
    retry_on=lambda error: _is_transient(error),
)

# Statuts HTTP qui valent une nouvelle tentative : quota, surcharge, panne amont.
_TRANSIENT_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

# Repli par nom de classe : chaque SDK a sa propre hiérarchie d'exceptions (openai,
# google.api_core, groq, httpx) et les importer toutes ici lierait le graphe à des
# dépendances optionnelles.
_TRANSIENT_NAMES = ("ratelimit", "timeout", "serviceunavailable", "internalserver", "apiconnection")


def _is_transient(error: BaseException) -> bool:
    """Faut-il réessayer ?

    **Un 400 ne doit jamais être réessayé** : la requête est invalide, la refaire trois
    fois ne fait que tripler la latence et la facture (vécu avec `reasoning_effort` sur
    /v1/chat/completions).
    """
    status = getattr(error, "status_code", None) or getattr(error, "code", None)
    if isinstance(status, int):
        return status in _TRANSIENT_STATUS

    name = type(error).__name__.lower()
    transient = any(marker in name for marker in _TRANSIENT_NAMES)
    if transient:
        logger.info("erreur transitoire, nouvelle tentative", extra={"erreur": name})
    return transient


class AgentState(MessagesState):
    """`messages` vient de MessagesState (réducteur add_messages).

    `loops` compte les tours de modèle et alimente le garde-fou de la boucle ReAct.
    """

    loops: int


def system_prompt(override: str | None = None) -> SystemMessage:
    """Prompt système du graphe.

    Args:
        override: surcharge venue de la configuration. Elle **remplace** le prompt
            par défaut en entier (pas de fusion) : c'est le seul comportement
            prévisible quand l'utilisateur réécrit les règles de l'agent.
    """
    if override:
        return SystemMessage(override)

    today = datetime.now(UTC).date().isoformat()
    return SystemMessage(
        "\n".join(
            [
                "Tu es un assistant de recherche francophone qui s'appuie sur des outils.",
                f"Date du jour : {today}.",
                "",
                "Règles :",
                "- Utilise un outil dès que la réponse dépend de faits, d'actualité, "
                "de météo ou d'un calcul.",
                "- Ne calcule jamais de tête : passe par `calculator`.",
                "- Tu peux enchaîner plusieurs outils avant de répondre.",
                "- Cite tes sources (titre + URL) quand elles viennent d'un outil.",
                "- Si un outil renvoie une erreur, explique-le au lieu d'inventer.",
                "- Réponds en français, en markdown, de façon concise.",
            ]
        )
    )


def _windowed(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Les derniers messages qui tiennent dans `MAX_CONTEXT_TOKENS`.

    `start_on="human"` n'est pas cosmétique : une fenêtre qui commencerait par un
    `ToolMessage` orphelin (son `AIMessage` avec les `tool_call_id` étant tombé hors
    fenêtre) est refusée par les providers. `include_system=False` parce que le prompt
    système n'est pas dans l'état — il est ajouté à l'appel, donc jamais rognable.
    """
    windowed = trim_messages(
        messages,
        max_tokens=MAX_CONTEXT_TOKENS,
        token_counter=count_tokens_approximately,
        strategy="last",
        start_on="human",
        include_system=False,
        allow_partial=False,
    )

    if len(windowed) < len(messages):
        logger.info(
            "historique rogné pour tenir dans la fenêtre",
            extra={
                "messages_recus": len(messages),
                "messages_envoyes": len(windowed),
                "plafond_tokens": MAX_CONTEXT_TOKENS,
            },
        )
    return windowed


def build_graph(model: BaseChatModel | None = None, config: settings.Settings | None = None):
    """Construit et compile le graphe.

    Args:
        model: modèle injectable — la prod passe `create_model()`, les tests un
            faux modèle à tool calling (voir tests/).
        config: configuration à appliquer. Par défaut le snapshot en mémoire
            (`settings.current()`), qui vaut les défauts si la base est absente :
            le chat ne dépend donc jamais de la disponibilité des réglages.
    """
    config = config or settings.current()

    # Un outil désactivé n'est ni déclaré au modèle ni exécutable : il disparaît
    # à la fois du bind_tools et du ToolNode.
    tools = [tool for tool in TOOLS if config.tools.get(tool.name, True)]

    # Outils MCP : lecture du cache alimenté par `settings.refresh()` — aucune I/O ici,
    # le chemin du chat doit rester synchrone (cf. agent/mcp.py).
    outils_mcp = mcp.tools()
    if outils_mcp:
        tools = [*tools, *outils_mcp]
        logger.info(
            "outils MCP bindés",
            extra={"mcp": len(outils_mcp), "total": len(tools)},
        )
    max_loops = config.agent.max_tool_loops
    prompt = system_prompt(config.agent.system_prompt)

    model = model or create_model(
        provider=config.model.provider,
        model=config.model.model,
        temperature=config.agent.temperature,
        reasoning_effort=config.model.reasoning_effort,
    )
    # bind_tools([]) n'a pas de sémantique commune entre providers : quand tout
    # est désactivé, on ne binde rien et le graphe se réduit à un aller simple.
    model_with_tools = model.bind_tools(tools) if tools else model

    async def agent(state: AgentState) -> dict:
        """Un tour de LLM : il répond, ou réclame des outils.

        On consomme `astream` plutôt que `ainvoke` : un `ainvoke` ne produit aucun
        token intermédiaire, donc le `stream_mode="messages"` de LangGraph n'aurait
        rien à transmettre et l'UI resterait figée jusqu'à la réponse complète.
        La somme des chunks reconstitue un AIMessageChunk complet, tool_calls parsés
        compris.
        """
        history = _windowed(state["messages"])

        response = None
        async for chunk in model_with_tools.astream([prompt, *history]):
            response = chunk if response is None else response + chunk

        if response is None:
            raise RuntimeError("Le modèle n'a produit aucune réponse")

        return {"messages": [response], "loops": state.get("loops", 0) + 1}

    def should_continue(state: AgentState) -> str:
        """Arête conditionnelle : boucle ReAct tant que le LLM réclame des outils."""
        last = state["messages"][-1]
        wants_tools = isinstance(last, AIMessage) and bool(last.tool_calls)
        return "tools" if wants_tools and state.get("loops", 0) <= max_loops else END

    builder = (
        StateGraph(AgentState)
        # La reprise vit sur le nœud, pas dans le code du nœud : c'est LangGraph qui
        # rejoue l'étape, avec son backoff, sans que `agent()` sache qu'il est rejoué.
        .add_node("agent", agent, retry_policy=AGENT_RETRY)
        .add_edge(START, "agent")
    )

    if tools:
        builder = (
            # `handle_tool_errors` remplace l'enveloppe maison qu'avait chaque outil :
            # centralisé ici, ça couvre aussi ce qu'un wrapper dans l'outil ne peut pas
            # voir — les erreurs de validation des arguments produits par le LLM.
            builder.add_node("tools", ToolNode(tools, handle_tool_errors=tool_error_message))
            .add_conditional_edges("agent", should_continue, ["tools", END])
            .add_edge("tools", "agent")
        )
    else:
        builder = builder.add_edge("agent", END)

    return builder.compile()


# (version de config, graphe compilé). `lru_cache` ne conviendrait pas : il n'y a
# pas d'argument à faire varier, donc le graphe resterait figé sur la config du
# démarrage et aucun réglage n'aurait d'effet avant un redémarrage du conteneur.
_cached: tuple[int, Any] | None = None


def get_graph():
    """Instanciation paresseuse : le serveur doit démarrer même sans clé API.

    Le graphe est reconstruit dès que `settings.version()` change, c'est-à-dire à
    chaque mutation de la configuration. Un échec de construction (clé API
    manquante) n'est pas mémorisé : la tentative suivante réessaie.
    """
    global _cached
    version = settings.version()
    if _cached is None or _cached[0] != version:
        logger.info("graphe (re)construit", extra={"version_config": version})
        _cached = (version, build_graph())
    return _cached[1]
