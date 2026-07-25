import { BookText, Calculator, CloudSun, Newspaper, WrenchIcon } from "lucide-react"
import { Section, SwitchRow } from "@/components/settings/controls"
import type { ToolSetting } from "@/components/settings/types"

/** Même correspondance que la sidebar : les icônes restent cohérentes d'un écran
 *  à l'autre. Un outil ajouté côté API sans entrée ici reste affiché (clé). */
const ICONS: Record<string, typeof WrenchIcon> = {
  wikipedia_search: BookText,
  hacker_news_search: Newspaper,
  weather_forecast: CloudSun,
  calculator: Calculator,
}

export function ToolsPanel({
  tools,
  disabled,
  onToggle,
}: {
  tools: ToolSetting[]
  disabled: boolean
  onToggle: (name: string, enabled: boolean) => void
}) {
  const active = tools.filter((tool) => tool.enabled).length

  return (
    <Section
      title={`Outils actifs (${active}/${tools.length})`}
      description="Un outil désactivé n'est plus déclaré au modèle : il ne peut donc plus être appelé, même si l'utilisateur le demande explicitement."
    >
      <ul className="flex flex-col gap-2">
        {tools.map((tool) => {
          const Icon = ICONS[tool.name] ?? WrenchIcon
          return (
            <li key={tool.name}>
              <SwitchRow
                checked={tool.enabled}
                disabled={disabled}
                description={tool.description}
                icon={<Icon className="size-4" />}
                onCheckedChange={(next) => onToggle(tool.name, next)}
                title={tool.name}
              />
            </li>
          )
        })}
      </ul>

      {active === 0 && (
        <p className="text-muted-foreground rounded-3xl border border-dashed px-3 py-2 text-xs">
          Aucun outil actif : l'agent répondra sans jamais consulter de source
          externe ni calculer.
        </p>
      )}
    </Section>
  )
}
