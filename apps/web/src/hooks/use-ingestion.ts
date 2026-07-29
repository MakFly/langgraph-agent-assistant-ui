import { useCallback, useEffect, useState } from "react"
import { toast } from "sonner"
import type {
  IngestionFile,
  IngestionRun,
  IngestionSource,
  IngestionState,
} from "@/components/settings/types"

async function errorMessage(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown }
    if (typeof body.detail === "string") return body.detail
    if (Array.isArray(body.detail)) {
      const first = body.detail[0] as { msg?: string } | undefined
      if (first?.msg) return first.msg
    }
  } catch {
    // Le statut HTTP reste une information exploitable.
  }
  return `HTTP ${response.status}`
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/ingestion${path}`, init)
  if (!response.ok) throw new Error(await errorMessage(response))
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export function useIngestion() {
  const [state, setState] = useState<
    | { state: "loading" }
    | { state: "error"; error: string }
    | { state: "ready"; data: IngestionState }
  >({ state: "loading" })
  const [files, setFiles] = useState<IngestionFile[]>([])
  const [runs, setRuns] = useState<IngestionRun[]>([])
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      const data = await request<IngestionState>("")
      setState({ state: "ready", data })
    } catch (error) {
      setState({
        state: "error",
        error: error instanceof Error ? error.message : "Erreur inconnue",
      })
    }
  }, [])

  const loadDetails = useCallback(async (sourceId: string) => {
    try {
      const [nextFiles, nextRuns] = await Promise.all([
        request<IngestionFile[]>(`/sources/${sourceId}/files`),
        request<IngestionRun[]>(`/sources/${sourceId}/runs`),
      ])
      setFiles(nextFiles)
      setRuns(nextRuns)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Chargement impossible")
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const mutate = useCallback(
    async <T,>(
      action: () => Promise<T>,
      success?: string,
    ): Promise<T | undefined> => {
      setBusy(true)
      try {
        const value = await action()
        if (success) toast.success(success)
        return value
      } catch (error) {
        toast.error(error instanceof Error ? error.message : "Opération impossible")
      } finally {
        setBusy(false)
      }
    },
    [],
  )

  return {
    state,
    files,
    runs,
    busy,
    reload: load,
    loadDetails,
    createSource: (name: string) =>
      mutate(
        () =>
          request<IngestionSource>("/sources", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ name, groups: ["public"] }),
          }),
        "Source créée",
      ),
    saveSource: (source: IngestionSource) =>
      mutate(
        () =>
          request<IngestionSource>(`/sources/${source.id}`, {
            method: "PATCH",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({
              name: source.name,
              groups: source.groups,
              enabled: source.enabled,
              ocr: source.ocr,
              options: source.options,
            }),
          }),
        "Source enregistrée",
      ),
    deleteSource: (sourceId: string) =>
      mutate(
        async () => {
          await request<void>(`/sources/${sourceId}`, { method: "DELETE" })
          return true
        },
        "Source et index supprimés",
      ),
    upload: (sourceId: string, group: string, file: File) =>
      mutate(async () => {
        const body = new FormData()
        body.append("file", file)
        return request<IngestionFile>(
          `/sources/${sourceId}/files?group=${encodeURIComponent(group)}`,
          { method: "POST", body },
        )
      }, "Fichier déposé"),
    deleteFile: (sourceId: string, group: string, filename: string) =>
      mutate(
        () =>
          request<void>(
            `/sources/${sourceId}/files/${encodeURIComponent(filename)}?group=${encodeURIComponent(group)}`,
            { method: "DELETE" },
          ),
        "Fichier supprimé — synchronisez pour le retirer de l’index",
      ),
    run: (sourceId: string, dryRun: boolean) =>
      mutate(
        () =>
          request<IngestionRun>(`/sources/${sourceId}/runs`, {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ dry_run: dryRun }),
          }),
        dryRun ? "Simulation lancée" : "Ingestion lancée",
      ),
  }
}
