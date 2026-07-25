import { useState } from "react"
import { SettingsIcon } from "lucide-react"
import { SettingsDialog } from "@/components/settings/settings-dialog"
import { SidebarMenuButton, SidebarMenuItem } from "@/components/ui/sidebar"

/**
 * Entrée du footer de la sidebar, à côté de <ThemeToggle />.
 *
 * L'état d'ouverture vit ici (et pas dans App.tsx) pour que l'ajout du panneau
 * ne touche pas la coque de l'application. `Dialog` de Radix ne rend aucun nœud
 * DOM tant qu'il est fermé : le placer à côté du <li> ne casse pas le <ul> de
 * SidebarMenu.
 */
export function SettingsMenuItem() {
  const [open, setOpen] = useState(false)

  return (
    <>
      <SidebarMenuItem>
        <SidebarMenuButton
          className="text-muted-foreground"
          onClick={() => setOpen(true)}
          size="sm"
          tooltip="Configuration"
        >
          <SettingsIcon />
          <span>Configuration</span>
        </SidebarMenuButton>
      </SidebarMenuItem>

      <SettingsDialog onOpenChange={setOpen} open={open} />
    </>
  )
}
