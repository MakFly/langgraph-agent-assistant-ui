import { useEffect, useState } from "react"
import { RotateCcwIcon } from "lucide-react"
import { Field, Section } from "@/components/settings/controls"
import type { AgentSettings } from "@/components/settings/types"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"

/**
 * Ces trois réglages ne s'enregistrent pas à la frappe : un prompt s'écrit en
 * plusieurs secondes, un PATCH par caractère n'aurait aucun sens (et rebâtirait
 * le graphe à chaque fois). D'où le brouillon local + bouton « Enregistrer ».
 */
export function AgentPanel({
  agent,
  disabled,
  saving,
  onSave,
}: {
  agent: AgentSettings
  disabled: boolean
  saving: boolean
  onSave: (body: {
    system_prompt?: string | null
    max_tool_loops?: number
    temperature?: number
    max_context_tokens?: number
  }) => void
}) {
  const [prompt, setPrompt] = useState(agent.system_prompt ?? "")
  const [loops, setLoops] = useState(String(agent.max_tool_loops))
  const [temperature, setTemperature] = useState(String(agent.temperature))
  const [context, setContext] = useState(String(agent.max_context_tokens))

  // L'API renvoie l'état complet après chaque mutation : on resynchronise le
  // brouillon dessus, ce qui évite d'afficher une valeur refusée par le serveur.
  useEffect(() => {
    setPrompt(agent.system_prompt ?? "")
    setLoops(String(agent.max_tool_loops))
    setTemperature(String(agent.temperature))
    setContext(String(agent.max_context_tokens))
  }, [agent])

  const [minLoops, maxLoops] = agent.max_tool_loops_range
  const [minTemp, maxTemp] = agent.temperature_range
  const [minContext, maxContext] = agent.max_context_tokens_range

  const dirty =
    prompt !== (agent.system_prompt ?? "") ||
    loops !== String(agent.max_tool_loops) ||
    temperature !== String(agent.temperature) ||
    context !== String(agent.max_context_tokens)

  // Mêmes bornes que la validation serveur : autant refuser localement plutôt
  // que d'aller chercher un 422.
  const loopsValue = Number(loops)
  const temperatureValue = Number(temperature)
  const contextValue = Number(context)
  const valid =
    Number.isInteger(loopsValue) &&
    loopsValue >= minLoops &&
    loopsValue <= maxLoops &&
    Number.isFinite(temperatureValue) &&
    temperature.trim() !== "" &&
    temperatureValue >= minTemp &&
    temperatureValue <= maxTemp &&
    Number.isInteger(contextValue) &&
    contextValue >= minContext &&
    contextValue <= maxContext

  return (
    <div className="flex flex-col gap-6">
      <Section
        title="Prompt système"
        description="Laissé vide, le prompt par défaut du graphe s'applique (assistant francophone, obligation de passer par les outils, citation des sources)."
      >
        <textarea
          className={cn(
            "bg-input/50 placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/30 min-h-40 w-full rounded-3xl border border-transparent px-3.5 py-2.5 text-base transition-[color,box-shadow,background-color] outline-none focus-visible:ring-3 disabled:opacity-50 md:text-sm",
            "resize-y",
          )}
          disabled={disabled}
          id="settings-system-prompt"
          onChange={(event) => setPrompt(event.target.value)}
          placeholder="Ex. : Tu es un analyste financier. Réponds toujours avec un tableau de synthèse."
          value={prompt}
        />
        {agent.system_prompt === null ? (
          <p className="text-muted-foreground text-xs">Prompt par défaut en vigueur.</p>
        ) : (
          <Button
            className="self-start pointer-coarse:min-h-11"
            disabled={disabled || saving}
            onClick={() => onSave({ system_prompt: "" })}
            size="sm"
            variant="ghost"
          >
            <RotateCcwIcon />
            Revenir au prompt par défaut
          </Button>
        )}
      </Section>

      <Section title="Boucle ReAct et créativité">
        {/* auto-fit : une colonne en mobile, deux dès qu'il y a la place, sans
            breakpoint à maintenir. */}
        <div className="grid gap-4 [grid-template-columns:repeat(auto-fit,minmax(11rem,1fr))]">
          <Field
            htmlFor="settings-max-loops"
            label="Tours d'outils maximum"
            hint={`Entre ${minLoops} et ${maxLoops}. Garde-fou contre un modèle qui boucle sur un outil en erreur.`}
          >
            <Input
              className="pointer-coarse:h-11"
              disabled={disabled}
              id="settings-max-loops"
              inputMode="numeric"
              max={maxLoops}
              min={minLoops}
              onChange={(event) => setLoops(event.target.value)}
              step={1}
              type="number"
              value={loops}
            />
          </Field>

          <Field
            htmlFor="settings-temperature"
            label="Température"
            hint={`Entre ${minTemp} et ${maxTemp}. 0 = déterministe, recommandé quand l'agent appelle des outils.`}
          >
            <Input
              className="pointer-coarse:h-11"
              disabled={disabled}
              id="settings-temperature"
              inputMode="decimal"
              max={maxTemp}
              min={minTemp}
              onChange={(event) => setTemperature(event.target.value)}
              step={0.1}
              type="number"
              value={temperature}
            />
          </Field>
        </div>
      </Section>

      <Section
        title="Fenêtre de contexte"
        description="Au-delà de ce plafond, les messages les plus anciens ne sont plus envoyés au modèle. À régler sur la fenêtre du modèle actif, en gardant de la marge : le prompt système et les schémas d'outils s'ajoutent à ce total (environ 700 tokens), et la réponse a besoin de place."
      >
        <div className="grid gap-4 [grid-template-columns:repeat(auto-fit,minmax(11rem,1fr))]">
          <Field
            htmlFor="settings-max-context"
            label="Historique envoyé (tokens)"
            hint={`Entre ${minContext.toLocaleString("fr-FR")} et ${maxContext.toLocaleString("fr-FR")}. Trop bas, l'agent perd le fil ; trop haut, le modèle refuse la requête.`}
          >
            <Input
              className="pointer-coarse:h-11"
              disabled={disabled}
              id="settings-max-context"
              inputMode="numeric"
              max={maxContext}
              min={minContext}
              onChange={(event) => setContext(event.target.value)}
              step={1000}
              type="number"
              value={context}
            />
          </Field>
        </div>
      </Section>

      <Button
        className="self-start pointer-coarse:min-h-11"
        disabled={disabled || saving || !dirty || !valid}
        onClick={() =>
          onSave({
            // Chaîne vide = « efface la surcharge », convention de l'API.
            system_prompt: prompt.trim() === "" ? "" : prompt,
            max_tool_loops: Number(loops),
            temperature: Number(temperature),
            max_context_tokens: Number(context),
          })
        }
      >
        Enregistrer
      </Button>
    </div>
  )
}
