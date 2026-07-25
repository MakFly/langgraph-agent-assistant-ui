import { useCallback, useEffect, useState } from "react"
import { toast } from "sonner"
import type {
  EffortLevel,
  McpServerInput,
  ProviderId,
  SettingsPayload,
} from "@/components/settings/types"
import { recallModel, rememberModel } from "@/lib/model-preference"

/**
 * État + mutations de la configuration globale.
 *
 * Deux choix assumés :
 *  - le chargement a lieu **au montage**, une seule fois pour toute l'application
 *    (un seul appelant : `<SettingsProvider>`). Il était paresseux tant que seul
 *    le panneau consommait ces données ; le sélecteur de modèle du composer, lui,
 *    est visible en permanence ;
 *  - toutes les mutations renvoient l'état complet côté serveur, donc on remplace
 *    le state par la réponse plutôt que de le recalculer localement. Pas de
 *    divergence possible entre ce qu'affiche l'UI et ce qui est enregistré.
 */
export type SettingsState =
  | { state: "loading" }
  | { state: "error"; error: string }
  | { state: "ready"; data: SettingsPayload }

/** Les erreurs FastAPI arrivent en `detail` : chaîne, ou liste d'erreurs pydantic. */
async function errorMessage(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown }
    const detail = body.detail
    if (typeof detail === "string") return detail
    if (Array.isArray(detail)) {
      const first = detail[0] as { msg?: string } | undefined
      if (first?.msg) return first.msg
    }
  } catch {
    // Corps non JSON : on retombe sur le code HTTP.
  }
  return `HTTP ${response.status}`
}

export function useSettings() {
  const [state, setState] = useState<SettingsState>({ state: "loading" })
  const [saving, setSaving] = useState(false)

  const load = useCallback(async (signal?: AbortSignal) => {
    setState({ state: "loading" })
    try {
      const response = await fetch("/api/settings", { signal })
      if (!response.ok) throw new Error(await errorMessage(response))
      setState({ state: "ready", data: (await response.json()) as SettingsPayload })
    } catch (error: unknown) {
      if (signal?.aborted) return
      setState({
        state: "error",
        error: error instanceof Error ? error.message : "inconnu",
      })
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    void load(controller.signal)
    return () => controller.abort()
  }, [load])

  /**
   * Toute mutation qui renvoie l'état complet. `refetch` sert aux endpoints MCP,
   * qui ne renvoient que la ressource touchée.
   */
  const mutate = useCallback(
    async (
      path: string,
      init: RequestInit,
      { refetch = false, success }: { refetch?: boolean; success?: string } = {},
    ): Promise<boolean> => {
      setSaving(true)
      try {
        const response = await fetch(`/api/settings${path}`, {
          headers: { "content-type": "application/json" },
          ...init,
        })
        if (!response.ok) throw new Error(await errorMessage(response))

        if (refetch) {
          await load()
        } else {
          setState({ state: "ready", data: (await response.json()) as SettingsPayload })
        }
        if (success) toast.success(success)
        return true
      } catch (error: unknown) {
        toast.error(error instanceof Error ? error.message : "Échec de l'enregistrement")
        return false
      } finally {
        setSaving(false)
      }
    },
    [load],
  )

  return {
    state,
    saving,
    reload: load,

    patchAgent: (body: {
      system_prompt?: string | null
      max_tool_loops?: number
      temperature?: number
    }) =>
      mutate("/agent", { method: "PATCH", body: JSON.stringify(body) }, {
        success: "Agent mis à jour",
      }),

    /**
     * Deux comportements s'ajoutent au PATCH, tous deux autour du couple
     * provider/modèle — que l'API, elle, patche champ par champ :
     *
     *  - changer de provider **sans** préciser de modèle réapplique le dernier
     *    modèle choisi pour ce provider (mémoire locale), sinon efface la
     *    surcharge (`""`). Sans ça, l'ancienne surcharge survivrait au changement
     *    de provider et on enverrait un modèle Groq à Gemini ;
     *  - tout choix explicite de modèle est mémorisé, d'où qu'il vienne (sélecteur
     *    du composer ou panneau de configuration).
     */
    patchModel: async (body: {
      provider?: ProviderId
      model?: string | null
      reasoning_effort?: EffortLevel
    }) => {
      const provider =
        body.provider ?? (state.state === "ready" ? state.data.model.provider : undefined)
      const model =
        body.model ?? (body.provider ? recallModel(body.provider) ?? "" : undefined)

      const ok = await mutate(
        "/model",
        {
          method: "PATCH",
          body: JSON.stringify(model === undefined ? body : { ...body, model }),
        },
        { success: "Modèle mis à jour" },
      )

      if (ok && provider && model) rememberModel(provider, model)
      return ok
    },

    toggleTool: (name: string, nextEnabled: boolean) =>
      mutate(`/tools/${encodeURIComponent(name)}`, {
        method: "PATCH",
        body: JSON.stringify({ enabled: nextEnabled }),
      }),

    createMcp: (body: McpServerInput) =>
      mutate("/mcp", { method: "POST", body: JSON.stringify(body) }, {
        refetch: true,
        success: "Serveur MCP ajouté",
      }),

    patchMcp: (id: string, body: Partial<McpServerInput>) =>
      mutate(`/mcp/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }, { refetch: true }),

    deleteMcp: (id: string) =>
      mutate(`/mcp/${encodeURIComponent(id)}`, { method: "DELETE" }, {
        refetch: true,
        success: "Serveur MCP supprimé",
      }),
  }
}

/** Ce que `<SettingsProvider>` publie dans le contexte. */
export type UseSettings = ReturnType<typeof useSettings>
