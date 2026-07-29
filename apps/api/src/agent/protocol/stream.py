"""Émission du « UI Message Stream » d'AI SDK, consommé nativement par assistant-ui.

Format du fil (mesuré sur l'implémentation TypeScript avant migration) :

    en-têtes : content-type: text/event-stream
               x-vercel-ai-ui-message-stream: v1
    corps    : `data: {"type":"text-delta","id":"0","delta":"Bon"}\\n\\n`
    fin      : `data: [DONE]\\n\\n`

Deux modes de stream LangGraph sont consommés en parallèle, chacun pour ce qu'il
fait le mieux :

  - `messages` : les tokens du LLM au fil de l'eau (texte + fragments d'arguments
    d'outil) — c'est ce qui donne l'effet de frappe et l'affichage progressif ;
  - `updates`  : l'état consolidé en sortie de chaque nœud — c'est là qu'on lit
    les arguments d'outil PARSÉS et les résultats d'outils, sans avoir à
    réassembler du JSON partiel.

Le bug qui a motivé la migration côté TS venait exactement de là : l'adaptateur
n'émettait jamais les arguments de l'appel d'outil, donc le tour suivant renvoyait
un `tool_call` sans `function.arguments` et l'API répondait 400. Ici on les émet
explicitement (`tool-input-available`), et un test le vérifie.

Une troisième source est branchée en fin de flux : le `usage_metadata` que le
provider renvoie avec sa réponse. C'est la **seule mesure exacte** du contexte
consommé — elle compte ce qui a réellement été envoyé, prompt système et schémas
d'outils compris, là où toute estimation « quatre caractères par token » les
ignore. Il part en `message-metadata`, la partie du protocole qu'assistant-ui lit
via `useThreadTokenUsage()`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from agent.core import settings, users
from agent.core.callbacks import RunMetricsHandler
from agent.core.graph import get_graph
from agent.core.rag.llm import INTERNAL_STREAM_TAG
from agent.core.users import User
from agent.protocol.messages import to_lc_messages

logger = logging.getLogger("agent.stream")

SSE_HEADERS = {
    "content-type": "text/event-stream",
    "cache-control": "no-cache",
    "connection": "keep-alive",
    "x-vercel-ai-ui-message-stream": "v1",
    # Empêche nginx / tout reverse proxy de bufferiser le flux.
    "x-accel-buffering": "no",
}


def sse(chunk: dict[str, Any]) -> str:
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"


# Marqueurs de « prompt trop long », tous providers confondus. Chacun a sa
# formulation ; aucun n'expose de code d'erreur exploitable pour ce cas précis.
_CONTEXT_MARKERS = (
    "context_length_exceeded",
    "context length",
    "maximum context",
    "too many tokens",
    "prompt is too long",
    "reduce the length",
    "input length and `max_tokens` exceed",
)


def friendly_error(error: BaseException) -> str:
    """Message destiné à l'utilisateur, à partir de l'erreur du provider.

    Un `context_length_exceeded` brut ressemble à une panne alors que c'est une
    limite atteinte, et il n'indique pas quoi faire. Le traduire coûte quelques
    lignes et transforme un message cryptique en instruction.

    La détection passe par le texte : c'est fragile par nature, mais les providers
    ne fournissent pas de code distinctif pour ce cas. En cas de non-reconnaissance
    on retombe sur le message d'origine — jamais sur un message inventé.
    """
    raw = str(error)
    lowered = raw.lower()

    if any(marker in lowered for marker in _CONTEXT_MARKERS):
        return (
            "Cette conversation dépasse la fenêtre de contexte du modèle. "
            "Démarrez une nouvelle conversation, ou réduisez la fenêtre "
            "d'historique dans les réglages (Agent → contexte)."
        )

    return raw


# Correspondance entre le `usage_metadata` normalisé par langchain et les clés que
# lit `useThreadTokenUsage()` d'assistant-ui (@assistant-ui/react-ai-sdk, usage.js).
# Les noms ne sont pas négociables : le hook ignore silencieusement tout le reste.
_USAGE_KEYS = (
    ("input_tokens", "inputTokens"),
    ("output_tokens", "outputTokens"),
    ("total_tokens", "totalTokens"),
)


def usage_payload(usage: dict[str, Any] | None) -> dict[str, int] | None:
    """Traduit le `usage_metadata` d'un provider en usage AI SDK, ou None.

    Rien n'est inventé : une clé absente reste absente. Un zéro fabriqué serait
    pire que pas de chiffre du tout — il se lirait comme une mesure.
    """
    if not usage:
        return None

    payload: dict[str, int] = {}
    for source, target in _USAGE_KEYS:
        value = usage.get(source)
        if isinstance(value, int) and value >= 0:
            payload[target] = value

    # Détails imbriqués, présents chez certains providers seulement.
    cached = (usage.get("input_token_details") or {}).get("cache_read")
    if isinstance(cached, int) and cached >= 0:
        payload["cachedInputTokens"] = cached

    reasoning = (usage.get("output_token_details") or {}).get("reasoning")
    if isinstance(reasoning, int) and reasoning >= 0:
        payload["reasoningTokens"] = reasoning

    return payload or None


def _chunk_text(chunk: AIMessageChunk) -> str:
    """Le contenu peut être une chaîne ou une liste de blocs selon le provider."""
    content = chunk.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


async def ui_message_stream(
    ui_messages: list[dict[str, Any]],
    graph: Any | None = None,
    user: User | None = None,
) -> AsyncIterator[str]:
    graph = graph or get_graph()

    # Identité du run, transportée par le `configurable` de LangGraph jusqu'aux
    # outils. C'est le seul canal : un outil ne lit ni cookie ni en-tête, il reçoit
    # les groupes en argument injecté (cf. agent.core.tools.rag).
    #
    # `user=None` produit une liste de groupes VIDE, jamais « tous les groupes » —
    # un appel sans identité ne doit rien pouvoir lire.
    identity = {
        "user_id": user.id if user else None,
        "user_groups": users.effective_groups(user) if user else [],
    }

    # Le client est la source de vérité de l'historique (pas de checkpointer,
    # cf. README « Détails d'implémentation »).
    history = to_lc_messages(ui_messages)

    yield sse({"type": "start"})
    yield sse({"type": "start-step"})

    text_id: str | None = None
    started_inputs: set[str] = set()
    announced_inputs: set[str] = set()
    emitted_outputs: set[str] = set()

    # Consommation réelle rapportée par le provider, du DERNIER tour de modèle.
    #
    # Le dernier, et non la somme des tours : cette valeur alimente une jauge de
    # *contexte*, et `input_tokens` du dernier appel est exactement la taille du
    # prompt envoyé — historique rogné, prompt système et schémas d'outils
    # compris. Sommer les tours donnerait un nombre bien plus grand que la
    # fenêtre réelle, puisque chaque tour renvoie tout l'historique.
    #
    # Corollaire à connaître : ce n'est donc PAS le coût du run. Pour facturer,
    # il faudrait additionner les tours, et c'est une autre question.
    usage: dict[str, Any] | None = None

    try:
        async for mode, payload in graph.astream(
            {"messages": history},
            stream_mode=["messages", "updates"],
            # Un handler par run : latence, temps jusqu'au premier token, tokens et
            # durée de chaque outil partent dans les logs sans polluer le graphe.
            config={"callbacks": [RunMetricsHandler()], "configurable": identity},
        ):
            if mode == "messages":
                chunk, metadata = payload
                if not isinstance(chunk, AIMessageChunk):
                    continue
                # Les LLM internes au RAG (HyDE, reformulation, reranking) sont
                # des détails d'implémentation. LangGraph peut remonter leurs
                # chunks imbriqués dans `stream_mode="messages"` : les exposer
                # montrerait une hypothèse potentiellement fausse et le JSON de
                # notation avant la vraie réponse.
                if INTERNAL_STREAM_TAG in (metadata.get("tags") or []):
                    continue

                # Fragments d'arguments d'outil : purement cosmétique (l'UI affiche
                # les arguments qui se construisent), la version parsée arrive après.
                for call in chunk.tool_call_chunks or []:
                    call_id = call.get("id")
                    if call_id and call_id not in started_inputs:
                        started_inputs.add(call_id)
                        yield sse(
                            {
                                "type": "tool-input-start",
                                "toolCallId": call_id,
                                "toolName": call.get("name") or "",
                                "dynamic": True,
                            }
                        )
                    if call.get("args"):
                        target = call_id or next(iter(started_inputs), None)
                        if target:
                            yield sse(
                                {
                                    "type": "tool-input-delta",
                                    "toolCallId": target,
                                    "inputTextDelta": call["args"],
                                }
                            )

                text = _chunk_text(chunk)
                if text:
                    if text_id is None:
                        text_id = "0"
                        yield sse({"type": "text-start", "id": text_id})
                    yield sse({"type": "text-delta", "id": text_id, "delta": text})

            elif mode == "updates":
                for node_output in payload.values():
                    for message in (node_output or {}).get("messages", []):
                        # Arguments d'outil parsés, en sortie du nœud agent.
                        if isinstance(message, AIMessage):
                            # Écrasement volontaire : on garde le dernier tour.
                            usage = getattr(message, "usage_metadata", None) or usage

                            for call in message.tool_calls or []:
                                call_id = call.get("id")
                                if not call_id or call_id in announced_inputs:
                                    continue
                                announced_inputs.add(call_id)
                                if call_id not in started_inputs:
                                    started_inputs.add(call_id)
                                    yield sse(
                                        {
                                            "type": "tool-input-start",
                                            "toolCallId": call_id,
                                            "toolName": call.get("name") or "",
                                            "dynamic": True,
                                        }
                                    )
                                yield sse(
                                    {
                                        "type": "tool-input-available",
                                        "toolCallId": call_id,
                                        "toolName": call.get("name") or "",
                                        "input": call.get("args") or {},
                                        "dynamic": True,
                                    }
                                )

                        # Résultats d'outils, en sortie du nœud tools.
                        elif isinstance(message, ToolMessage):
                            call_id = message.tool_call_id
                            if not call_id or call_id in emitted_outputs:
                                continue
                            emitted_outputs.add(call_id)
                            if message.status == "error":
                                yield sse(
                                    {
                                        "type": "tool-output-error",
                                        "toolCallId": call_id,
                                        "errorText": str(message.content),
                                        "dynamic": True,
                                    }
                                )
                            else:
                                yield sse(
                                    {
                                        "type": "tool-output-available",
                                        "toolCallId": call_id,
                                        "output": message.content,
                                        "dynamic": True,
                                    }
                                )

    except Exception as error:  # noqa: BLE001 - l'erreur doit atteindre l'UI ET les logs
        # `yield` seul, c'était le trou : la réponse est un 200 qui streame, donc un
        # échec du provider (400 sur un paramètre, quota, clé invalide) n'apparaissait
        # NI dans les logs uvicorn NI dans Dozzle. Le contexte du modèle est joint :
        # sans lui, un « 400 invalid_request_error » n'est pas diagnosticable.
        config = settings.current()
        logger.exception(
            "run interrompu : %s",
            error,
            extra={
                "provider": config.model.provider,
                "modele": config.model.model or "défaut du provider",
                "effort": config.model.reasoning_effort,
                "outils": len(settings.enabled_tools()),
                "messages": len(ui_messages),
            },
        )
        # L'utilisateur reçoit une formulation actionnable ; les logs, eux, gardent
        # la trace complète du message d'origine (`logger.exception` ci-dessus).
        yield sse({"type": "error", "errorText": friendly_error(error)})

    if text_id is not None:
        yield sse({"type": "text-end", "id": text_id})

    # Consommation réelle, attachée au message assistant. Émise AVANT `finish` :
    # le client applique `message-metadata` au message en cours (cf. `ai@7`,
    # `updateMessageMetadata`), et après `finish` il n'y en a plus.
    #
    # C'est la seule mesure exacte du contexte dont dispose l'interface. Tout le
    # reste — côté serveur comme côté client — est une approximation à quatre
    # caractères par token, qui ignore le prompt système et les schémas d'outils.
    measured = usage_payload(usage)
    if measured is not None:
        yield sse(
            {"type": "message-metadata", "messageMetadata": {"usage": measured}}
        )
        logger.debug("consommation rapportée par le provider", extra=measured)

    yield sse({"type": "finish-step"})
    yield sse({"type": "finish"})
    yield "data: [DONE]\n\n"
