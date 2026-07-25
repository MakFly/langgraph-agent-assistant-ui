import type { FC } from "react"
import { cn } from "@/lib/utils"
import type { HealthState } from "@/hooks/use-health"

const LABEL: Record<HealthState["state"], string> = {
  loading: "connexion…",
  online: "en ligne",
  offline: "hors ligne",
}

const DOT: Record<HealthState["state"], string> = {
  loading: "bg-muted-foreground/50",
  online: "bg-emerald-500",
  offline: "bg-destructive",
}

export const HealthBadge: FC<{ health: HealthState }> = ({ health }) => (
  <p
    className="text-muted-foreground flex items-center gap-1.5 text-xs"
    role="status"
    title={
      health.state === "online"
        ? `Provider ${health.data.provider} · historisation ${health.data.history ? "active" : "désactivée"}`
        : undefined
    }
  >
    <span className={cn("size-2 rounded-full", DOT[health.state])} aria-hidden />
    <span className="hidden sm:inline">
      {health.state === "online" ? `${health.data.provider} · ` : ""}
    </span>
    {LABEL[health.state]}
  </p>
)
