import { createContext, useContext, type PropsWithChildren } from "react"
import { useSettings, type UseSettings } from "@/hooks/use-settings"

/**
 * Un seul état de configuration pour toute l'application.
 *
 * Deux consommateurs lisent le même couple provider/modèle : le panneau de
 * configuration et le sélecteur de modèle du composer. Avec un `useSettings()`
 * par consommateur, un changement fait d'un côté laisserait l'autre sur une
 * valeur périmée — d'où ce contexte plutôt que deux états parallèles.
 */
const SettingsContext = createContext<UseSettings | null>(null)

export function SettingsProvider({ children }: PropsWithChildren) {
  const settings = useSettings()
  return <SettingsContext.Provider value={settings}>{children}</SettingsContext.Provider>
}

export function useSettingsContext(): UseSettings {
  const settings = useContext(SettingsContext)
  if (!settings) {
    throw new Error("useSettingsContext() doit être appelé sous <SettingsProvider>")
  }
  return settings
}
