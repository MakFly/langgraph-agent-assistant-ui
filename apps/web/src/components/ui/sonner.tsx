
import type { CSSProperties } from "react"
import { Toaster as Sonner, type ToasterProps } from "sonner"

/*
 * Palette : les toasts neutres, `info` et `success` prennent le bleu informatif
 * (tokens `--info*`, style Geist/Vercel) ; `error` et `warning` gardent les
 * couleurs sémantiques de sonner (richColors) pour rester lisibles comme alerte.
 * Les variables sont branchées sur les tokens du thème → le dark mode suit la
 * classe `.dark` sans passer par la prop `theme` de sonner.
 */
const infoColors = {
  "--normal-bg": "var(--info)",
  "--normal-border": "var(--info-border)",
  "--normal-text": "var(--info-foreground)",
  "--info-bg": "var(--info)",
  "--info-border": "var(--info-border)",
  "--info-text": "var(--info-foreground)",
  "--success-bg": "var(--info)",
  "--success-border": "var(--info-border)",
  "--success-text": "var(--info-foreground)",
} as CSSProperties

export function Toaster({ ...props }: ToasterProps) {
  return (
    <Sonner
      className="toaster group"
      position="bottom-right"
      richColors
      mobileOffset={{ bottom: "max(1rem, env(safe-area-inset-bottom))", left: "1rem", right: "1rem" }}
      style={infoColors}
      {...props}
    />
  )
}
