import { useAuiState } from "@assistant-ui/react";
import { useThreadTokenUsage } from "@assistant-ui/react-ai-sdk";
import { useSettingsContext } from "@/components/settings/settings-context";
import { estimateTokens, remainingPercent } from "@/lib/context-usage";
import { cn } from "@/lib/utils";

/** En dessous, la couleur passe en destructive : le rognage est proche. */
const SEUIL_ALERTE = 15;

/**
 * Contexte restant, dans la barre d'actions du composer.
 *
 * **Deux sources, dans cet ordre.**
 *
 * 1. `useThreadTokenUsage()` — la consommation que le provider a réellement
 *    rapportée, transmise par le backend en `message-metadata`
 *    (`agent/protocol/stream.py`). C'est la seule mesure exacte : elle compte le
 *    prompt système et les schémas d'outils, que toute estimation ignore.
 *    On lit `totalTokens` et non `inputTokens` : ce qui repartira au tour suivant,
 *    c'est le prompt précédent **plus** la réponse qui vient d'être produite.
 *
 * 2. À défaut, l'estimation locale — avant la première réponse de l'assistant, ou
 *    avec un provider qui ne rapporte pas ses tokens en streaming (le cas existe,
 *    cf. langchain#30429). Elle sous-évalue systématiquement, et l'infobulle le dit.
 *
 * Le plafond, lui, vient toujours de l'API (`agent.context_window_tokens`) et
 * n'est jamais dupliqué ici : c'est le serveur qui rogne, l'UI rend sa limite
 * visible.
 *
 * Rien n'est affiché sur une conversation vide : un « 100 % » permanent serait du bruit.
 */
export function ComposerContextMeter() {
  const { state } = useSettingsContext();
  const messages = useAuiState((s) => s.thread.messages);
  const usage = useThreadTokenUsage();

  if (state.state !== "ready") return null;

  const budget = state.data.agent.context_window_tokens;
  const measured = usage?.totalTokens;
  const used = measured ?? estimateTokens(messages);
  if (used === 0) return null;

  const percent = remainingPercent(used, budget);
  const alerte = percent <= SEUIL_ALERTE;

  const chiffres = `${used.toLocaleString("fr-FR")} / ${budget.toLocaleString("fr-FR")} tokens`;
  // Le pourcentage seul est ambigu : le détail chiffré lève le doute, et dire
  // d'où vient le nombre évite de faire passer une estimation pour une mesure.
  const detail =
    measured !== undefined
      ? `${chiffres} (mesuré par le modèle)`
      : `~${chiffres} (estimé — sous-évalue le prompt système et les outils)`;

  return (
    <span
      className={cn(
        "shrink-0 text-xs tabular-nums",
        alerte ? "text-destructive font-medium" : "text-muted-foreground",
      )}
      title={
        percent === 0
          ? `Contexte plein — ${detail}. Les messages les plus anciens ne sont plus envoyés au modèle.`
          : detail
      }
    >
      <span aria-hidden>{percent} %</span>
      <span className="sr-only">
        {percent} pour cent de contexte restant, {detail}
      </span>
    </span>
  );
}
