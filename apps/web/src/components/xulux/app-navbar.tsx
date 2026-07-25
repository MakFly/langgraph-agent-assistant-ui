import { PlusIcon } from "lucide-react"
import { cn } from "@/lib/utils"
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbList,
  BreadcrumbPage,
} from "@/components/ui/breadcrumb"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { CustomSidebarTrigger } from "@/components/xulux/custom-sidebar-trigger"
import { NavUser } from "@/components/xulux/nav-user"
import { HealthBadge } from "@/components/xulux/health-badge"
import type { HealthState } from "@/hooks/use-health"

// Écarts assumés avec la source : pas de routeur ici, donc pas de `usePathname` —
// le titre arrive en prop. La recherche cmdk est remplacée par l'action
// « nouvelle conversation », la seule qui ait du sens sur un chat.
export function AppNavbar({
  title,
  health,
  onNewThread,
}: {
  title: string
  health: HealthState
  onNewThread: () => void
}) {
  return (
    <header
      className={cn(
        "sticky top-0 z-50 flex h-14 shrink-0 items-center justify-between gap-2 px-4 md:px-6",
      )}
    >
      <div className="flex min-w-0 items-center gap-3">
        <CustomSidebarTrigger />
        <Separator
          className="mr-2 h-4 data-[orientation=vertical]:self-center"
          orientation="vertical"
        />
        <Breadcrumb>
          <BreadcrumbList>
            <BreadcrumbItem>
              <BreadcrumbPage className="truncate">{title}</BreadcrumbPage>
            </BreadcrumbItem>
          </BreadcrumbList>
        </Breadcrumb>
      </div>

      <div className="flex items-center gap-3">
        <HealthBadge health={health} />
        <Button
          aria-label="Nouvelle conversation"
          className="pointer-coarse:size-11"
          onClick={onNewThread}
          size="icon-sm"
          variant="outline"
        >
          <PlusIcon />
        </Button>
        <Separator
          className="h-4 data-[orientation=vertical]:self-center"
          orientation="vertical"
        />
        <NavUser />
      </div>
    </header>
  )
}
