import { ThreadList } from "@/components/assistant-ui/thread-list"
import { SettingsMenuItem } from "@/components/settings/settings-menu-item"
import { LogoIcon } from "@/components/xulux/logo"
import { ThemeToggle } from "@/components/xulux/theme-toggle"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"
import type { HealthState } from "@/hooks/use-health"
import { CHAT_BASE } from "@/lib/chat-route"
import { resolveToolMeta } from "@/lib/tool-metadata"

// Icône et libellé viennent du registre partagé ; ici on ne fixe que la liste
// et l'ordre des outils affichés dans la sidebar.
const TOOL_NAMES = [
  "document_search",
  "wikipedia_search",
  "hacker_news_search",
  "weather_forecast",
  "calculator",
] as const
const TOOLS = TOOL_NAMES.map((name) => {
  const meta = resolveToolMeta(name)
  return { name, label: meta.label, icon: meta.icon }
})

/**
 * Structure reprise de la sidebar xulux (en-tête logo, groupes, footer). Le
 * contenu change : à la place des sections de navigation, la liste des
 * conversations d'assistant-ui, puisque cette application n'a qu'une seule page.
 */
export function AppSidebar({ health }: { health: HealthState }) {
  const liveTools = health.state === "online" ? health.data.tools : []

  return (
    <Sidebar collapsible="icon" variant="inset">
      <SidebarHeader>
        {/* Écart assumé avec la maquette xulux : elle transforme le logo en
            trigger de sidebar au survol en mode icône. On l'a retiré parce qu'il
            faisait doublon avec le trigger de la navbar — deux contrôles
            « Toggle Sidebar » actifs en même temps. C'est celui de la navbar qui
            reste : il est atteignable en permanence, alors que celui-ci
            n'apparaissait qu'au survol quand la sidebar était repliée. */}
        <div className="flex items-center gap-2">
          <SidebarMenuButton asChild className="min-w-0 flex-1">
            {/* `CHAT_BASE` et non « / » : la racine ne fait que rediriger, autant
                viser directement la bonne URL. */}
            <a href={CHAT_BASE}>
              <LogoIcon />
              <span className="font-medium">LangChain</span>
            </a>
          </SidebarMenuButton>
        </div>
      </SidebarHeader>

      <SidebarContent>
        {/* La liste gère elle-même « nouvelle conversation », la recherche,
            l'archivage et la suppression — tout passe par l'API /api/threads. */}
        <SidebarGroup className="min-h-0 flex-1 group-data-[collapsible=icon]:hidden">
          <ThreadList />
        </SidebarGroup>

        <SidebarGroup className="group-data-[collapsible=icon]:hidden">
          <SidebarGroupLabel>Outils de l'agent</SidebarGroupLabel>
          <SidebarMenu>
            {TOOLS.map((tool) => {
              const Icon = tool.icon
              const unavailable = health.state === "online" && !liveTools.includes(tool.name)

              return (
                <SidebarMenuItem key={tool.name}>
                  <SidebarMenuButton
                    className="text-muted-foreground cursor-default"
                    size="sm"
                    tooltip={tool.name}
                  >
                    <Icon />
                    <span>{tool.label}</span>
                    {unavailable && (
                      <span className="text-destructive ms-auto text-[11px]">off</span>
                    )}
                  </SidebarMenuButton>
                </SidebarMenuItem>
              )
            })}
          </SidebarMenu>
        </SidebarGroup>
      </SidebarContent>

      <SidebarFooter>
        <SidebarMenu>
          <SettingsMenuItem />
          <ThemeToggle />
        </SidebarMenu>
      </SidebarFooter>
    </Sidebar>
  )
}
