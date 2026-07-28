import type { ThreadMessage } from "@assistant-ui/react";

/**
 * Estimation du contexte consommé — **le plan B**.
 *
 * La mesure exacte vient du provider (`usage_metadata`), transmise par le backend
 * en `message-metadata` et lue par `useThreadTokenUsage()`. Ce module ne sert que
 * tant qu'aucune mesure n'existe : avant la première réponse de l'assistant, ou
 * avec un provider qui ne rapporte pas ses tokens en streaming.
 *
 * **Copie volontaire** de la formule de `count_tokens_approximately`
 * (langchain-core), celle-là même qui décide du rognage côté serveur
 * (`agent.settings.MAX_CONTEXT_TOKENS`) : estimer autrement donnerait un chiffre
 * qui ne correspond à rien de ce que le serveur applique.
 *
 *     par message : ceil(caractères / 4) + 3
 *     caractères  : texte + rôle + arguments et résultats d'outils
 *
 * Trois angles morts connus, tous dans le même sens — l'estimation **sous-évalue**
 * toujours : le prompt système n'est pas compté (le serveur le rogne hors fenêtre
 * puis l'ajoute à l'appel), les schémas d'outils envoyés par `bind_tools()` non
 * plus, et les images valent zéro. C'est précisément ce que la mesure du provider
 * corrige.
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

/**
 * Part de fenêtre restante, en pourcentage entier.
 *
 * 100 = rien de consommé, 0 = les plus anciens messages sont rognés côté serveur.
 * Séparé du décompte pour servir indifféremment une valeur mesurée ou estimée.
 */
export function remainingPercent(used: number, budget: number): number {
  // `budget` vient de l'API ; on se protège d'un 0 qui produirait un NaN.
  const remaining = budget > 0 ? 1 - used / budget : 1;
  return Math.max(0, Math.min(100, Math.round(remaining * 100)));
}
