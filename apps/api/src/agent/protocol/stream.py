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
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from agent.core import settings
from agent.core.callbacks import RunMetricsHandler
from agent.core.graph import get_graph
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
) -> AsyncIterator[str]:
    graph = graph or get_graph()

    # Le client est la source de vérité de l'historique (pas de checkpointer,
    # cf. README « Détails d'implémentation »).
    history = to_lc_messages(ui_messages)

    yield sse({"type": "start"})
    yield sse({"type": "start-step"})

    text_id: str | None = None
    started_inputs: set[str] = set()
    announced_inputs: set[str] = set()
    emitted_outputs: set[str] = set()

    try:
        async for mode, payload in graph.astream(
            {"messages": history},
            stream_mode=["messages", "updates"],
            # Un handler par run : latence, temps jusqu'au premier token, tokens et
            # durée de chaque outil partent dans les logs sans polluer le graphe.
            config={"callbacks": [RunMetricsHandler()]},
        ):
            if mode == "messages":
                chunk, _metadata = payload
                if not isinstance(chunk, AIMessageChunk):
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
        yield sse({"type": "error", "errorText": str(error)})

    if text_id is not None:
        yield sse({"type": "text-end", "id": text_id})

    yield sse({"type": "finish-step"})
    yield sse({"type": "finish"})
    yield "data: [DONE]\n\n"
