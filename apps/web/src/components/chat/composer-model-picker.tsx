import { CpuIcon } from "lucide-react";
import { ModelSelect } from "@/components/settings/model-select";
import { useSettingsContext } from "@/components/settings/settings-context";

/**
 * Sélecteur de modèle, dans la barre d'actions du composer.
 *
 * Il ne liste que les modèles du **provider courant** (celui de la configuration,
 * changeable dans l'onglet « Modèle ») : c'est le provider qui décide des modèles
 * disponibles, pas l'inverse. Le choix part en `PATCH /api/settings/model` — donc
 * il agit vraiment sur le graphe — et se mémorise en localStorage, par provider
 * (voir `patchModel()` dans use-settings.ts).
 *
 * La configuration étant globale côté serveur, ce sélecteur affiche toujours la
 * valeur réellement active : pas de vitrine locale qui prétendrait avoir changé de
 * modèle sans que l'agent suive.
 */
export function ComposerModelPicker() {
  const { state, saving, patchModel } = useSettingsContext();

  // Le chat ne dépend jamais de la configuration (cf. docs/settings.md) : tant
  // qu'elle n'est pas lue — ou si elle est illisible — on n'affiche rien plutôt que
  // de bloquer ou d'inventer une valeur.
  if (state.state !== "ready") return null;

  const { model, persisted } = state.data;

  return (
    <ModelSelect
      ariaLabel="Modèle utilisé"
      // Largeur bornée en rem (pas en %) : dans un flex, un max-width relatif se
      // résoudrait contre un parent dont la largeur dépend justement du contenu.
      className="w-auto min-w-0 max-w-36 gap-1.5 border-transparent bg-transparent px-2 text-xs text-muted-foreground hover:bg-muted sm:max-w-56 sm:text-sm pointer-coarse:h-11"
      // Sans base joignable, rien ne peut être enregistré : lecture seule, comme le
      // panneau de configuration.
      disabled={!persisted || saving}
      model={model}
      onSelect={(name) => void patchModel({ model: name })}
      // Le composer est en bas de l'écran : la liste s'ouvre vers le haut.
      side="top"
    >
      <CpuIcon className="size-3.5 shrink-0 opacity-70" />
    </ModelSelect>
  );
}
