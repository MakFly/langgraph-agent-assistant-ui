import type { ReactNode } from "react"
import type { ModelSettings, ProviderId } from "@/components/settings/types"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

/** Noms courts : le composer n'a pas la place des libellés descriptifs du panneau. */
export const PROVIDER_NAMES: Record<ProviderId, string> = {
  groq: "Groq",
  google: "Google Gemini",
  ollama: "Ollama",
  openai: "OpenAI",
}

/**
 * Choix du modèle parmi ceux du **provider courant**.
 *
 * Partagé par le composer et l'onglet « Modèle » : la construction de la liste, le
 * repli sur la valeur active et le contournement du faux événement de Radix doivent
 * rester identiques des deux côtés — dupliqués, ils divergeraient au premier
 * correctif.
 */
export function ModelSelect({
  model,
  disabled,
  onSelect,
  className,
  id,
  ariaLabel,
  side,
  children,
}: {
  model: ModelSettings
  disabled: boolean
  onSelect: (name: string) => void
  className?: string
  /** À cibler par un `<label htmlFor>` dans le panneau. */
  id?: string
  /** Seul nom accessible quand il n'y a pas de `<label>` visible (composer). */
  ariaLabel?: string
  /** `top` pour le composer, collé au bas de l'écran. */
  side?: "top" | "bottom"
  /** Contenu ajouté dans le déclencheur, avant la valeur (icône du composer). */
  children?: ReactNode
}) {
  const provider = model.providers.find((entry) => entry.id === model.provider)
  const effective = model.effective_model ?? ""

  // Une surcharge saisie à la main n'appartient pas au catalogue : on l'ajoute pour
  // que la valeur active reste toujours représentable.
  const names = [...new Set([...(provider?.models ?? []), effective].filter(Boolean))]

  return (
    <Select
      // Nouvelle instance par provider : la sélection ne traverse jamais un état où
      // la valeur active n'appartient pas encore à la liste d'items.
      key={model.provider}
      disabled={disabled}
      // Changer de provider remplace toute la liste d'items : Radix perd alors sa
      // sélection et émet une valeur vide. Ce n'est pas un choix de l'utilisateur —
      // sans ce garde-fou, ce faux événement effaçait le modèle qui venait d'être
      // restauré (constaté : un `PATCH {"model":""}` suivait chaque bascule).
      onValueChange={(next) => {
        if (!next || next === effective) return
        onSelect(next)
      }}
      value={effective}
    >
      <SelectTrigger aria-label={ariaLabel} className={className} id={id}>
        {children}
        <SelectValue />
      </SelectTrigger>

      <SelectContent align="start" className="max-w-[min(22rem,90dvw)]" side={side}>
        <SelectGroup>
          <SelectLabel>{PROVIDER_NAMES[model.provider] ?? model.provider}</SelectLabel>
          {names.map((name) => (
            <SelectItem className="pointer-coarse:min-h-11" key={name} value={name}>
              <span className="truncate font-mono text-xs">{name}</span>
            </SelectItem>
          ))}
        </SelectGroup>
      </SelectContent>
    </Select>
  )
}
