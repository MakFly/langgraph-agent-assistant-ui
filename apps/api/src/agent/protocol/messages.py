"""Conversion `UIMessage[]` (assistant-ui / AI SDK) -> messages LangChain.

Côté TypeScript, `@ai-sdk/langchain` faisait ce travail. Il n'a pas d'équivalent
Python : on le réimplémente, sur la base du format réellement émis par le client
(capturé sur le fil, pas déduit) :

    {"role": "user",      "parts": [{"type": "text", "text": "..."}]}
    {"role": "assistant", "parts": [
        {"type": "step-start"},
        {"type": "dynamic-tool", "toolName": "...", "toolCallId": "...",
         "state": "output-available", "input": {...}, "output": "..."},
        {"type": "text", "text": "...", "state": "done"}]}

Le point délicat : un seul message assistant côté UI contient à la fois l'appel
d'outil ET la réponse finale. Les APIs de chat exigent la séquence
`AIMessage(tool_calls)` -> `ToolMessage` -> `AIMessage(texte)`. On découpe donc
en plusieurs messages, dans l'ordre des parts.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

# Un appel d'outil sans résultat casserait la requête (toute tool_call doit avoir
# sa réponse), donc on ne garde que les états terminaux.
COMPLETED_STATES = {"output-available", "output-error"}


def _is_tool_part(part_type: str) -> bool:
    """`dynamic-tool` pour les outils déclarés côté serveur, `tool-<nom>` si typés."""
    return part_type == "dynamic-tool" or part_type.startswith("tool-")


def _tool_name(part: dict[str, Any]) -> str:
    return part.get("toolName") or part.get("type", "").removeprefix("tool-")


def _as_text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _collect_text(parts: list[dict[str, Any]]) -> str:
    return "".join(part.get("text") or "" for part in parts if part.get("type") == "text")


def _assistant_messages(parts: list[dict[str, Any]]) -> list[BaseMessage]:
    out: list[BaseMessage] = []
    text_buffer: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    tool_results: list[ToolMessage] = []

    def flush_text() -> None:
        text = "".join(text_buffer)
        text_buffer.clear()
        if text:
            out.append(AIMessage(content=text))

    def flush_tools() -> None:
        if not tool_calls:
            return
        out.append(AIMessage(content="", tool_calls=list(tool_calls)))
        out.extend(tool_results)
        tool_calls.clear()
        tool_results.clear()

    for part in parts:
        part_type = part.get("type", "")

        if part_type == "text":
            # Le texte clôt le tour d'outils précédent.
            flush_tools()
            text_buffer.append(part.get("text") or "")

        elif _is_tool_part(part_type):
            if part.get("state") not in COMPLETED_STATES:
                continue
            call_id = part.get("toolCallId")
            if not call_id:
                continue

            flush_text()
            name = _tool_name(part)
            tool_calls.append({"name": name, "args": part.get("input") or {}, "id": call_id})

            if part.get("state") == "output-error":
                content, status = part.get("errorText") or "erreur inconnue", "error"
            else:
                content, status = _as_text(part.get("output")), "success"

            tool_results.append(
                ToolMessage(content=content, tool_call_id=call_id, name=name, status=status)
            )

    flush_tools()
    flush_text()
    return out


def to_lc_messages(ui_messages: list[dict[str, Any]]) -> list[BaseMessage]:
    out: list[BaseMessage] = []

    for message in ui_messages:
        role = message.get("role")
        parts = message.get("parts") or []

        if role == "user":
            text = _collect_text(parts)
            if text:
                out.append(HumanMessage(content=text))

        elif role == "system":
            text = _collect_text(parts)
            if text:
                out.append(SystemMessage(content=text))

        elif role == "assistant":
            out.extend(_assistant_messages(parts))

    return out
