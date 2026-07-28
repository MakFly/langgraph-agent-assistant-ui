import { BracesIcon } from "lucide-react"
import { Section, SwitchRow } from "@/components/settings/controls"
import type { ToolSetting } from "@/components/settings/types"
import { useToolDetails } from "@/hooks/use-tool-details"
import { resolveToolMeta } from "@/lib/tool-metadata"

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
  // Préférence d'affichage locale (jamais désactivée par le mode lecture seule :
  // elle ne dépend pas de Postgres, c'est du confort de lecture côté navigateur).
  const { enabled: showToolDetails, setEnabled: setShowToolDetails } =
    useToolDetails()

  return (
    <div className="flex flex-col gap-5">
      <Section
        title={`Outils actifs (${active}/${tools.length})`}
        description="Un outil désactivé n'est plus déclaré au modèle : il ne peut donc plus être appelé, même si l'utilisateur le demande explicitement."
      >
        <ul className="flex flex-col gap-2">
          {tools.map((tool) => {
            const Icon = resolveToolMeta(tool.name).icon
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

      <Section
        title="Affichage"
        description="Comment les appels d'outils apparaissent dans la conversation."
      >
        <SwitchRow
          checked={showToolDetails}
          onCheckedChange={setShowToolDetails}
          icon={<BracesIcon className="size-4" />}
          title="Détails techniques des outils"
          description="Affiche les paramètres et le résultat brut de chaque appel. Désactivé, seule une ligne d'activité lisible est montrée (recommandé)."
        />
      </Section>
    </div>
  )
}
