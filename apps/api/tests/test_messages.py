"""Conversion UIMessage -> LangChain.

Les charges utiles ci-dessous reprennent le format réellement émis par le client
assistant-ui, capturé sur le fil avant la migration — pas une reconstitution.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.protocol.messages import to_lc_messages

TOOL_TURN = [
    {
        "id": "u1",
        "role": "user",
        "parts": [{"type": "text", "text": "Météo à Lyon ?"}],
    },
    {
        "id": "a1",
        "role": "assistant",
        "parts": [
            {"type": "step-start"},
            {
                "type": "dynamic-tool",
                "toolName": "weather_forecast",
                "toolCallId": "call_abc",
                "state": "output-available",
                "input": {"city": "Lyon, France"},
                "output": '{"location":"Lyon"}',
            },
            {"type": "step-start"},
            {"type": "text", "text": "Il fait 30°C à Lyon.", "state": "done"},
        ],
    },
    {
        "id": "u2",
        "role": "user",
        "parts": [{"type": "text", "text": "et à Paris ?"}],
    },
]


def test_les_arguments_de_l_outil_survivent_a_la_conversion():
    """Régression : c'est l'absence de ces arguments qui renvoyait un 400.

    Un `tool_call` sans arguments fait répondre à l'API du modèle
    « Missing required parameter: messages[2].tool_calls[0].function.arguments ».
    """
    messages = to_lc_messages(TOOL_TURN)
    ai_with_tools = next(m for m in messages if isinstance(m, AIMessage) and m.tool_calls)

    assert ai_with_tools.tool_calls[0]["name"] == "weather_forecast"
    assert ai_with_tools.tool_calls[0]["args"] == {"city": "Lyon, France"}
    assert ai_with_tools.tool_calls[0]["id"] == "call_abc"


def test_l_ordre_attendu_par_les_apis_de_chat_est_respecte():
    """Un message assistant côté UI porte l'appel ET la réponse : il faut le découper."""
    messages = to_lc_messages(TOOL_TURN)

    assert [type(m).__name__ for m in messages] == [
        "HumanMessage",  # « Météo à Lyon ? »
        "AIMessage",  # l'appel d'outil, contenu vide
        "ToolMessage",  # son résultat
        "AIMessage",  # la réponse finale
        "HumanMessage",  # « et à Paris ? »
    ]

    ai_call, tool_result, ai_text = messages[1], messages[2], messages[3]
    assert ai_call.content == ""
    assert isinstance(tool_result, ToolMessage)
    assert tool_result.tool_call_id == "call_abc"
    assert ai_text.content == "Il fait 30°C à Lyon."
    assert not ai_text.tool_calls


def test_un_appel_d_outil_sans_resultat_est_ignore():
    """Une tool_call sans ToolMessage correspondant fait échouer la requête amont."""
    messages = to_lc_messages(
        [
            {
                "role": "assistant",
                "parts": [
                    {
                        "type": "dynamic-tool",
                        "toolName": "weather_forecast",
                        "toolCallId": "call_incomplet",
                        "state": "input-available",
                        "input": {"city": "Lyon"},
                    }
                ],
            }
        ]
    )

    assert messages == []


def test_appels_paralleles_regroupes_dans_un_seul_message():
    messages = to_lc_messages(
        [
            {
                "role": "assistant",
                "parts": [
                    {
                        "type": "dynamic-tool",
                        "toolName": "weather_forecast",
                        "toolCallId": "c1",
                        "state": "output-available",
                        "input": {"city": "Lyon"},
                        "output": "{}",
                    },
                    {
                        "type": "dynamic-tool",
                        "toolName": "calculator",
                        "toolCallId": "c2",
                        "state": "output-available",
                        "input": {"expression": "2+2"},
                        "output": "{}",
                    },
                ],
            }
        ]
    )

    assert [type(m).__name__ for m in messages] == ["AIMessage", "ToolMessage", "ToolMessage"]
    assert [call["id"] for call in messages[0].tool_calls] == ["c1", "c2"]


def test_erreur_d_outil_conservee():
    messages = to_lc_messages(
        [
            {
                "role": "assistant",
                "parts": [
                    {
                        "type": "dynamic-tool",
                        "toolName": "calculator",
                        "toolCallId": "c3",
                        "state": "output-error",
                        "input": {"expression": "??"},
                        "errorText": "Expression invalide",
                    }
                ],
            }
        ]
    )

    tool_message = messages[1]
    assert tool_message.status == "error"
    assert "Expression invalide" in tool_message.content


def test_message_utilisateur_simple():
    messages = to_lc_messages([{"role": "user", "parts": [{"type": "text", "text": "salut"}]}])
    assert messages == [HumanMessage(content="salut")]
