import type { ThreadMessage } from "@assistant-ui/react";

/**
 * Estimation du contexte consommé par une conversation.
 *
 * **Copie volontaire** de la formule de `count_tokens_approximately`
 * (langchain-core), celle-là même qui décide du rognage côté serveur
 * (`agent.settings.MAX_CONTEXT_TOKENS`) : afficher un pourcentage calculé autrement
 * donnerait un chiffre qui ne correspond à rien de ce que le serveur applique.
 *
 *     par message : ceil(caractères / 4) + 3
 *     caractères  : texte + rôle + arguments et résultats d'outils
 *
 * Reste une **estimation** : le serveur compte des messages LangChain reconstruits par
 * `to_lc_messages()`, pas les parts de l'UI, et un tokenizer réel diverge de « 4
 * caractères par token ». Assez juste pour une jauge, jamais pour une facturation.
 */

const CHARS_PER_TOKEN = 4;
const EXTRA_TOKENS_PER_MESSAGE = 3;

function partChars(part: ThreadMessage["content"][number]): number {
  switch (part.type) {
    case "text":
    case "reasoning":
      return part.text.length;
    case "tool-call":
      // Les arguments et le résultat repartent au modèle à chaque tour : ils pèsent
      // souvent plus que le texte visible.
      return (
        (part.toolName?.length ?? 0) +
        JSON.stringify(part.args ?? {}).length +
        JSON.stringify(part.result ?? "").length
      );
    default:
      return 0;
  }
}

export function estimateTokens(messages: readonly ThreadMessage[]): number {
  return messages.reduce((total, message) => {
    const chars =
      message.role.length +
      message.content.reduce((sum, part) => sum + partChars(part), 0);
    return total + Math.ceil(chars / CHARS_PER_TOKEN) + EXTRA_TOKENS_PER_MESSAGE;
  }, 0);
}

export type ContextUsage = {
  used: number;
  budget: number;
  /** 100 = rien de consommé, 0 = les plus anciens messages sont rognés. */
  remainingPercent: number;
};

export function contextUsage(
  messages: readonly ThreadMessage[],
  budget: number,
): ContextUsage {
  const used = estimateTokens(messages);
  // `budget` vient de l'API ; on se protège d'un 0 qui produirait un NaN à l'affichage.
  const remaining = budget > 0 ? 1 - used / budget : 1;
  return {
    used,
    budget,
    remainingPercent: Math.max(0, Math.min(100, Math.round(remaining * 100))),
  };
}
