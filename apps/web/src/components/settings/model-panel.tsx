import { useEffect, useState } from "react"
import { CheckIcon, KeyRoundIcon, TriangleAlertIcon } from "lucide-react"
import { Field, Section } from "@/components/settings/controls"
import { ModelSelect, PROVIDER_NAMES } from "@/components/settings/model-select"
import type { EffortLevel, ModelSettings, ProviderId } from "@/components/settings/types"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

const PROVIDER_LABELS: Record<ProviderId, string> = {
  groq: "Groq — gratuit, le plus rapide",
  google: "Google Gemini — gratuit, quota large",
  ollama: "Ollama — 100 % local",
  openai: "OpenAI — payant à l'usage",
}

const EFFORT_LABELS: Record<string, string> = {
  low: "Faible — le plus rapide",
  medium: "Moyen",
  high: "Élevé — le plus lent",
}

export function ModelPanel({
  model,
  disabled,
  saving,
  onSave,
}: {
  model: ModelSettings
  disabled: boolean
  saving: boolean
  onSave: (body: {
    provider?: ProviderId
    model?: string | null
    reasoning_effort?: EffortLevel
  }) => void
}) {
  const [override, setOverride] = useState(model.model ?? "")

  useEffect(() => setOverride(model.model ?? ""), [model])

  const selected = model.providers.find((provider) => provider.id === model.provider)
  const dirty = override !== (model.model ?? "")
  // C'est l'API qui tranche : les paliers dépendent du modèle actif, pas seulement
  // du provider (cf. agent.model.EFFORT_MODELS).
  const supportsEffort = model.effort_levels.length > 0

  return (
    <div className="flex flex-col gap-6">
      <Section
        title="Provider"
        description="Le changement s'applique immédiatement : le graphe est reconstruit au prochain message, sans redémarrage du conteneur."
      >
        <Select
          disabled={disabled}
          onValueChange={(value) => onSave({ provider: value as ProviderId })}
          value={model.provider}
        >
          <SelectTrigger className="pointer-coarse:h-11" id="settings-provider">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {model.providers.map((provider) => (
              <SelectItem key={provider.id} value={provider.id}>
                {PROVIDER_LABELS[provider.id] ?? provider.id}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {/* Aucune valeur de clé n'arrive jusqu'ici : l'API n'expose qu'un booléen. */}
        <ul className="flex flex-col gap-1.5">
          {model.providers.map((provider) => (
            <li
              className="text-muted-foreground flex min-h-8 items-center gap-2 text-xs"
              key={provider.id}
            >
              {provider.has_key ? (
                <CheckIcon aria-hidden className="text-primary size-3.5 shrink-0" />
              ) : (
                <KeyRoundIcon aria-hidden className="text-destructive size-3.5 shrink-0" />
              )}
              <span className="font-mono">{provider.id}</span>
              <span className="truncate">
                {provider.requires_key
                  ? provider.has_key
                    ? "clé configurée"
                    : "clé absente de apps/api/.env"
                  : "aucune clé nécessaire"}
              </span>
            </li>
          ))}
        </ul>

        {selected && !selected.has_key && (
          <p className="text-destructive flex items-start gap-2 rounded-3xl border border-dashed px-3 py-2 text-xs">
            <TriangleAlertIcon aria-hidden className="mt-0.5 size-3.5 shrink-0" />
            <span>
              Aucune clé pour ce provider : le chat échouera à la première réponse.
              Renseignez-la dans <code className="font-mono">apps/api/.env</code>, puis
              redémarrez le service api.
            </span>
          </p>
        )}
      </Section>

      <Section
        title="Modèle"
        description="Les modèles récents du provider sélectionné. Liste indicative, tenue à la main côté API : elle n'est pas exhaustive et rien ne l'impose."
      >
        <Field
          htmlFor="settings-model-catalog"
          label={`Modèles ${PROVIDER_NAMES[model.provider] ?? model.provider}`}
          hint={
            <>
              S'applique immédiatement, comme le sélecteur du composer. Modèle effectif :{" "}
              <code className="font-mono">{model.effective_model ?? "—"}</code>.
            </>
          }
        >
          <ModelSelect
            className="pointer-coarse:h-11"
            disabled={disabled}
            id="settings-model-catalog"
            model={model}
            onSelect={(name) => onSave({ model: name })}
          />
        </Field>

        <Field
          htmlFor="settings-effort"
          label="Effort de raisonnement"
          hint={
            supportsEffort ? (
              "Plus d'effort = plus de tokens de raisonnement et de latence, réponses plus posées."
            ) : (
              <>
                <code className="font-mono">{model.effective_model ?? "—"}</code> n'expose
                pas de palier d'effort : le provider refuserait le paramètre.
              </>
            )
          }
        >
          <Select
            disabled={disabled || !supportsEffort}
            // Même garde-fou que <ModelSelect> : la liste d'items change avec le
            // modèle, et Radix émet alors une valeur vide qui n'est pas un choix.
            onValueChange={(value) => {
              if (!value || value === model.reasoning_effort) return
              onSave({ reasoning_effort: value as EffortLevel })
            }}
            value={model.reasoning_effort}
          >
            <SelectTrigger className="pointer-coarse:h-11" id="settings-effort">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="default">Défaut du modèle</SelectItem>
              {model.effort_levels.map((level) => (
                <SelectItem key={level} value={level}>
                  {EFFORT_LABELS[level] ?? level}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>

        <Field
          htmlFor="settings-model"
          label="Autre modèle"
          hint={
            <>
              Nom libre, pour un modèle absent de la liste — indispensable pour un tag
              Ollama local. Vide = défaut du provider (
              <code className="font-mono">{selected?.default_model ?? "—"}</code>).
            </>
          }
        >
          <Input
            className="pointer-coarse:h-11"
            disabled={disabled}
            id="settings-model"
            onChange={(event) => setOverride(event.target.value)}
            placeholder={selected?.default_model ?? "nom du modèle"}
            value={override}
          />
        </Field>

        <Button
          className="self-start pointer-coarse:min-h-11"
          disabled={disabled || saving || !dirty}
          // Chaîne vide = « efface la surcharge », convention de l'API.
          onClick={() => onSave({ model: override.trim() })}
        >
          Enregistrer
        </Button>
      </Section>
    </div>
  )
}
