import { useState } from "react"
import { BotIcon, CpuIcon, DatabaseZapIcon, ServerIcon, WrenchIcon } from "lucide-react"
import { AgentPanel } from "@/components/settings/agent-panel"
import { McpPanel } from "@/components/settings/mcp-panel"
import { ModelPanel } from "@/components/settings/model-panel"
import { useSettingsContext } from "@/components/settings/settings-context"
import { ToolsPanel } from "@/components/settings/tools-panel"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

const TABS = [
  { id: "tools", label: "Outils", icon: WrenchIcon },
  { id: "agent", label: "Agent", icon: BotIcon },
  { id: "model", label: "Modèle", icon: CpuIcon },
  { id: "mcp", label: "MCP", icon: ServerIcon },
] as const

type TabId = (typeof TABS)[number]["id"]

/**
 * Panneau de configuration.
 *
 * Mobile-first : plein écran en `h-dvh` (jamais `vh`, la barre d'URL d'iOS
 * fausserait la hauteur), puis modale centrée dès `sm` : **50 % de large, 85 % de
 * haut**, en unités dynamiques (`dvw`/`dvh`) par cohérence avec le reste. L'overlay
 * est volontairement sans `backdrop-blur` — sur un overlay plein écran, le flou
 * coûte des frames sur les GPU mobiles milieu de gamme.
 *
 * Le contenu des onglets reste borné à `max-w-3xl` : c'est un plafond de longueur
 * de ligne, utile si la modale est un jour élargie — la modale grandit, pas la
 * longueur de ligne.
 *
 * Il n'y a pas de primitive `tabs` dans components/ui/ et la consigne est de ne pas
 * ajouter de dépendance : les onglets sont donc une `tablist` ARIA maison, avec
 * navigation clavier native (les boutons sont dans l'ordre du DOM).
 */
export function SettingsDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [tab, setTab] = useState<TabId>("tools")
  const settings = useSettingsContext()
  const { state, saving } = settings

  const readOnly = state.state !== "ready" || !state.data.persisted

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent
        aria-describedby="settings-description"
        className={cn(
          // Mobile : plein écran, coins droits, pas de translation.
          "top-0 left-0 flex h-dvh max-h-dvh w-full max-w-full translate-x-0 translate-y-0 flex-col gap-0 rounded-none p-0",
          // Tablette et au-delà : modale centrée, colonne étroite et haute —
          // 50 % de la largeur, 85 % de la hauteur du viewport.
          "sm:top-1/2 sm:left-1/2 sm:h-[85dvh] sm:w-[50dvw] sm:max-w-[50dvw] sm:-translate-x-1/2 sm:-translate-y-1/2 sm:rounded-4xl",
        )}
        // Pas de flou sur un overlay plein écran (perf GPU mobile).
        overlayClassName="supports-backdrop-filter:backdrop-blur-none"
      >
        <DialogHeader className="gap-1 px-4 pt-[max(1rem,env(safe-area-inset-top))] pr-14 sm:px-6 sm:pt-6">
          <DialogTitle>Configuration</DialogTitle>
          <DialogDescription id="settings-description">
            Réglages globaux de l'agent. Ils s'appliquent immédiatement, sans
            redémarrage du service.
          </DialogDescription>
        </DialogHeader>

        {/* Onglets : rangée défilable en mobile, jamais tronquée. */}
        <div
          aria-label="Sections de configuration"
          className="flex gap-1 overflow-x-auto border-b px-4 pt-3 pb-3 sm:px-6 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
          role="tablist"
        >
          {TABS.map(({ id, label, icon: Icon }) => (
            <button
              aria-controls={`settings-panel-${id}`}
              aria-selected={tab === id}
              className={cn(
                "flex shrink-0 items-center gap-1.5 rounded-4xl px-3 py-1.5 text-sm font-medium transition-colors",
                "focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/30 focus-visible:outline-none",
                "pointer-coarse:min-h-11 pointer-coarse:px-4",
                tab === id
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-muted",
              )}
              id={`settings-tab-${id}`}
              key={id}
              onClick={() => setTab(id)}
              role="tab"
              type="button"
            >
              <Icon className="size-4" />
              {label}
            </button>
          ))}
        </div>

        <div
          aria-labelledby={`settings-tab-${tab}`}
          className="flex-1 overflow-y-auto overscroll-contain px-4 py-5 pb-[max(1.25rem,env(safe-area-inset-bottom))] sm:px-6"
          id={`settings-panel-${tab}`}
          role="tabpanel"
          tabIndex={0}
        >
          {state.state === "loading" && (
            <div className="flex w-full max-w-3xl flex-col gap-3">
              {[0, 1, 2, 3].map((index) => (
                <Skeleton className="h-14 w-full rounded-3xl" key={index} />
              ))}
            </div>
          )}

          {state.state === "error" && (
            <div className="flex w-full max-w-3xl flex-col items-start gap-3">
              <p className="text-destructive text-sm">
                Configuration illisible : {state.error}
              </p>
              <Button
                className="pointer-coarse:min-h-11"
                onClick={() => void settings.reload()}
                size="sm"
                variant="outline"
              >
                Réessayer
              </Button>
            </div>
          )}

          {state.state === "ready" && (
            <div className="flex w-full max-w-3xl flex-col gap-5">
              {!state.data.persisted && (
                <p className="text-muted-foreground flex items-start gap-2 rounded-3xl border border-dashed px-3 py-2 text-xs">
                  <DatabaseZapIcon aria-hidden className="mt-0.5 size-3.5 shrink-0" />
                  <span>
                    Postgres est injoignable : l'agent tourne avec les valeurs par
                    défaut et rien ne peut être enregistré. Le chat, lui, fonctionne.
                  </span>
                </p>
              )}

              {tab === "tools" && (
                <ToolsPanel
                  disabled={readOnly}
                  onToggle={(name, enabled) => void settings.toggleTool(name, enabled)}
                  tools={state.data.tools}
                />
              )}

              {tab === "agent" && (
                <AgentPanel
                  agent={state.data.agent}
                  disabled={readOnly}
                  onSave={(body) => void settings.patchAgent(body)}
                  saving={saving}
                />
              )}

              {tab === "model" && (
                <ModelPanel
                  disabled={readOnly}
                  model={state.data.model}
                  onSave={(body) => void settings.patchModel(body)}
                  saving={saving}
                />
              )}

              {tab === "mcp" && (
                <McpPanel
                  disabled={readOnly}
                  onCreate={settings.createMcp}
                  onDelete={(id) => void settings.deleteMcp(id)}
                  onToggle={(id, enabled) => void settings.patchMcp(id, { enabled })}
                  saving={saving}
                  servers={state.data.mcp_servers}
                />
              )}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
