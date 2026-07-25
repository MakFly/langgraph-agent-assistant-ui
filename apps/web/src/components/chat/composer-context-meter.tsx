import { useAuiState } from "@assistant-ui/react";
import { useSettingsContext } from "@/components/settings/settings-context";
import { contextUsage } from "@/lib/context-usage";
import { cn } from "@/lib/utils";

/** En dessous, la couleur passe en destructive : le rognage est proche. */
const SEUIL_ALERTE = 15;

/**
 * Contexte restant, dans la barre d'actions du composer.
 *
 * Le plafond vient de l'API (`agent.context_window_tokens`) et non d'une constante
 * dupliquée ici : c'est le serveur qui rogne l'historique, l'UI ne fait que rendre
 * visible sa limite. À 0 %, les messages les plus anciens ne sont plus envoyés au
 * modèle — c'est précisément ce qu'un utilisateur doit pouvoir anticiper.
 *
 * Rien n'est affiché sur une conversation vide : un « 100 % » permanent serait du bruit.
 */
export function ComposerContextMeter() {
  const { state } = useSettingsContext();
  const messages = useAuiState((s) => s.thread.messages);

  if (state.state !== "ready") return null;

  const budget = state.data.agent.context_window_tokens;
  const { used, remainingPercent } = contextUsage(messages, budget);
  if (used === 0) return null;

  const alerte = remainingPercent <= SEUIL_ALERTE;

  return (
    <span
      className={cn(
        "shrink-0 text-xs tabular-nums",
        alerte ? "text-destructive font-medium" : "text-muted-foreground",
      )}
      // Le pourcentage seul est ambigu ; le détail chiffré lève le doute, et le titre
      // explique ce qui se passe quand la fenêtre est pleine.
      title={
        remainingPercent === 0
          ? `Contexte plein (~${used.toLocaleString("fr-FR")} / ${budget.toLocaleString("fr-FR")} tokens estimés) : les messages les plus anciens ne sont plus envoyés au modèle.`
          : `~${used.toLocaleString("fr-FR")} / ${budget.toLocaleString("fr-FR")} tokens estimés`
      }
    >
      <span aria-hidden>{remainingPercent} %</span>
      <span className="sr-only">
        {remainingPercent} pour cent de contexte restant
      </span>
    </span>
  );
}
