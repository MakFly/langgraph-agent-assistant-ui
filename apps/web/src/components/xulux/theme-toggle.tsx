import { MoonIcon, SunIcon } from "lucide-react"
import { SidebarMenuButton, SidebarMenuItem } from "@/components/ui/sidebar"
import { useTheme } from "@/hooks/use-theme"

// Écart assumé avec la source : next-themes n'a pas lieu d'être ici (pas de SSR).
// Le hook local pose la classe `dark` avant le premier paint, via index.html.
export function ThemeToggle() {
  const { dark, toggle } = useTheme()

  return (
    <SidebarMenuItem>
      <SidebarMenuButton
        className="text-muted-foreground"
        onClick={toggle}
        size="sm"
        tooltip="Changer de thème"
      >
        <SunIcon className="scale-100 rotate-0 transition-transform dark:scale-0 dark:-rotate-90" />
        <MoonIcon className="absolute scale-0 rotate-90 transition-transform dark:scale-100 dark:rotate-0" />
        <span>{dark ? "Thème sombre" : "Thème clair"}</span>
      </SidebarMenuButton>
    </SidebarMenuItem>
  )
}
