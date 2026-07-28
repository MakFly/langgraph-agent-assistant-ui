"""Faux modèle à tool calling.

`langchain_core` fournit des fakes, mais aucun ne combine ce dont on a besoin ici :
appels d'outils ET streaming en `tool_call_chunks` comme le ferait un vrai provider.
Ce fake reproduit ce comportement, ce qui permet de tester le chemin de production
complet sans clé API.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Sequence
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import Field


def tool_call(name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


class FakeToolCallingModel(BaseChatModel):
    """Rejoue une file de réponses, une par invocation."""

    responses: list[BaseMessage] = Field(default_factory=list)
    call_log: list[Any] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "fake-tool-calling"

    @property
    def call_count(self) -> int:
        return len(self.call_log)

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> BaseChatModel:
        return self

    def _next(self, messages: list[BaseMessage]) -> BaseMessage:
        index = len(self.call_log)
        self.call_log.append(messages)
        if index >= len(self.responses):
            raise AssertionError(
                f"Le modèle a été appelé {index + 1} fois pour {len(self.responses)} réponses"
            )
        return self.responses[index]

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._next(messages))])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        message = self._next(messages)

        if isinstance(message, AIMessage) and message.tool_calls:
            # Comme un vrai provider : les arguments arrivent en fragments JSON.
            for index, call in enumerate(message.tool_calls):
                yield ChatGenerationChunk(
                    message=AIMessageChunk(
                        content="",
                        tool_call_chunks=[
                            {
                                "name": call["name"],
                                "args": json.dumps(call["args"]),
                                "id": call["id"],
                                "index": index,
                                "type": "tool_call_chunk",
                            }
                        ],
                    )
                )
            yield from self._usage_chunk(message)
            return

        # Découpage qui préserve les espaces : la concaténation des chunks
        # doit redonner le texte à l'identique.
        text = message.content if isinstance(message.content, str) else ""
        for piece in re.findall(r"\S+\s*", text):
            yield ChatGenerationChunk(message=AIMessageChunk(content=piece))

        yield from self._usage_chunk(message)

    @staticmethod
    def _usage_chunk(message: BaseMessage) -> Iterator[ChatGenerationChunk]:
        """Dernier fragment, porteur de la consommation — comme un vrai provider.

        Les providers rapportent les tokens **à la fin** du flux, dans un chunk
        sans contenu. Reproduire ce détail est nécessaire : sans lui, la partie
        `message-metadata` du protocole n'a rien à transporter et le test
        vérifierait un chemin qui n'existe pas en production.
        """
        usage = getattr(message, "usage_metadata", None)
        if usage:
            yield ChatGenerationChunk(
                message=AIMessageChunk(content="", usage_metadata=usage)
            )
