import type { PropsWithChildren, ReactNode } from "react"
import { cn } from "@/lib/utils"

/**
 * Petits contrôles propres au panneau de configuration.
 *
 * Il n'y a pas de primitive `switch` ni `label` dans `components/ui/`, et la
 * consigne est de ne pas ajouter de dépendance UI : ces deux-là sont donc écrits
 * ici, avec la même grammaire visuelle que les primitives existantes (rayons
 * `rounded-3xl`, anneau de focus `ring-ring/30`, tokens de thème).
 */

/** Ligne cliquable entière : la cible tactile fait toute la largeur, jamais 20px. */
export function SwitchRow({
  checked,
  onCheckedChange,
  disabled = false,
  title,
  description,
  icon,
}: {
  checked: boolean
  onCheckedChange: (next: boolean) => void
  disabled?: boolean
  title: string
  description?: ReactNode
  icon?: ReactNode
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onCheckedChange(!checked)}
      className={cn(
        "group flex w-full min-h-11 items-center gap-3 rounded-3xl border border-border/60 px-3 py-2.5 text-start transition-colors",
        "hover:bg-muted/60 focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/30 focus-visible:outline-none",
        "disabled:pointer-events-none disabled:opacity-50",
      )}
    >
      {icon && <span className="text-muted-foreground shrink-0">{icon}</span>}
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium">{title}</span>
        {description && (
          // line-clamp : les descriptions d'outils sont des docstrings entières,
          // deux lignes suffisent à qualifier la ligne sans la faire exploser.
          <span className="text-muted-foreground mt-0.5 line-clamp-2 block text-xs leading-snug">
            {description}
          </span>
        )}
      </span>
      <span
        aria-hidden
        data-checked={checked}
        className={cn(
          "relative h-6 w-11 shrink-0 rounded-full transition-colors",
          "bg-input data-[checked=true]:bg-primary",
        )}
      >
        <span
          className={cn(
            "bg-background absolute top-0.5 left-0.5 size-5 rounded-full shadow-sm transition-transform",
            checked && "translate-x-5",
          )}
        />
      </span>
    </button>
  )
}

/** Libellé + aide + contrôle, empilés en mobile, alignés au-dessus en desktop. */
export function Field({
  label,
  hint,
  htmlFor,
  children,
}: PropsWithChildren<{ label: string; hint?: ReactNode; htmlFor?: string }>) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-sm font-medium" htmlFor={htmlFor}>
        {label}
      </label>
      {children}
      {hint && <p className="text-muted-foreground text-xs leading-snug">{hint}</p>}
    </div>
  )
}

/** Titre de section, pour séparer les blocs d'un onglet sans surcharger le DOM. */
export function Section({
  title,
  description,
  children,
}: PropsWithChildren<{ title: string; description?: ReactNode }>) {
  return (
    <section className="flex flex-col gap-3">
      <header className="flex flex-col gap-0.5">
        <h3 className="font-heading text-sm font-medium">{title}</h3>
        {description && (
          <p className="text-muted-foreground text-xs leading-snug">{description}</p>
        )}
      </header>
      {children}
    </section>
  )
}
