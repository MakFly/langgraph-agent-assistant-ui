import { useEffect, useMemo, useState } from "react"
import {
  AlertTriangleIcon,
  CheckCircle2Icon,
  FileTextIcon,
  LoaderCircleIcon,
  PlusIcon,
  ScanTextIcon,
  Trash2Icon,
  UploadIcon,
} from "lucide-react"
import { Field, Section, SwitchRow } from "@/components/settings/controls"
import type { IngestionSource, ProviderId } from "@/components/settings/types"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { useIngestion } from "@/hooks/use-ingestion"
import { cn } from "@/lib/utils"

const formatBytes = (bytes: number) =>
  new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 1 }).format(bytes / 1024) +
  " Ko"

const statusLabel = {
  queued: "En attente",
  running: "En cours",
  succeeded: "Terminé",
  failed: "Échec",
} as const

export function SourcesPanel() {
  const api = useIngestion()
  const { loadDetails } = api
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [draft, setDraft] = useState<IngestionSource | null>(null)
  const [newName, setNewName] = useState("")
  const [upload, setUpload] = useState<File | null>(null)
  const [uploadGroup, setUploadGroup] = useState("public")

  const sources = api.state.state === "ready" ? api.state.data.sources : []
  const selected = sources.find((source) => source.id === selectedId) ?? sources[0]

  useEffect(() => {
    if (!selected) {
      setSelectedId(null)
      setDraft(null)
      return
    }
    if (selected.id !== selectedId) setSelectedId(selected.id)
    setDraft(selected)
    setUploadGroup(selected.groups[0] ?? "public")
    void loadDetails(selected.id)
  }, [loadDetails, selected, selectedId])

  const activeRun = api.runs.some(
    (run) => run.status === "queued" || run.status === "running",
  )

  useEffect(() => {
    if (!selected?.id || !activeRun) return
    const timer = window.setInterval(() => void loadDetails(selected.id), 1500)
    return () => window.clearInterval(timer)
  }, [activeRun, loadDetails, selected?.id])

  const provider = useMemo(
    () =>
      api.state.state === "ready" && draft
        ? api.state.data.providers.find((item) => item.id === draft.ocr.provider)
        : undefined,
    [api.state, draft],
  )

  if (api.state.state === "loading") {
    return (
      <div className="grid gap-4 md:grid-cols-[15rem_1fr]">
        <Skeleton className="h-64 rounded-3xl" />
        <Skeleton className="h-96 rounded-3xl" />
      </div>
    )
  }

  if (api.state.state === "error") {
    return (
      <div className="flex flex-col items-start gap-3">
        <p className="text-destructive text-sm">{api.state.error}</p>
        <Button onClick={() => void api.reload()} size="sm" variant="outline">
          Réessayer
        </Button>
      </div>
    )
  }

  const data = api.state.data

  const refresh = async (sourceId?: string) => {
    await api.reload()
    if (sourceId) await api.loadDetails(sourceId)
  }

  const create = async () => {
    if (!newName.trim()) return
    const created = await api.createSource(newName.trim())
    if (!created) return
    setNewName("")
    setSelectedId(created.id)
    await refresh(created.id)
  }

  const save = async () => {
    if (!draft) return
    const saved = await api.saveSource(draft)
    if (saved) {
      setDraft(saved)
      await refresh(saved.id)
    }
  }

  return (
    <div className="grid min-w-0 gap-6 md:grid-cols-[15rem_minmax(0,1fr)]">
      <aside className="flex min-w-0 flex-col gap-3 md:border-r md:pr-5">
        <div className="flex gap-2">
          <Input
            aria-label="Nom de la nouvelle source"
            onChange={(event) => setNewName(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void create()
            }}
            placeholder="Nouvelle source"
            value={newName}
          />
          <Button
            aria-label="Créer la source"
            disabled={api.busy || !newName.trim()}
            onClick={() => void create()}
            size="icon"
          >
            <PlusIcon aria-hidden />
          </Button>
        </div>

        <nav aria-label="Sources documentaires" className="flex flex-col gap-1">
          {sources.map((source) => (
            <button
              className={cn(
                "flex min-h-11 w-full items-center gap-2 rounded-2xl px-3 text-left text-sm",
                "focus-visible:ring-3 focus-visible:ring-ring/30 focus-visible:outline-none",
                selected?.id === source.id
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-muted",
              )}
              key={source.id}
              onClick={() => setSelectedId(source.id)}
              type="button"
            >
              <FileTextIcon aria-hidden className="size-4 shrink-0" />
              <span className="min-w-0 flex-1 truncate">{source.name}</span>
              {!source.enabled && <span className="text-[10px]">off</span>}
            </button>
          ))}
        </nav>

        {sources.length === 0 && (
          <p className="text-muted-foreground px-2 py-6 text-center text-xs">
            Créez une source pour déposer vos premiers documents.
          </p>
        )}
      </aside>

      {draft && selected ? (
        <div className="flex min-w-0 flex-col gap-7">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b pb-4">
            <div>
              <h3 className="font-heading font-medium">{draft.name}</h3>
              <p className="text-muted-foreground text-xs">
                Source upload · {api.files.length} fichier(s)
              </p>
            </div>
            <div className="flex gap-2">
              <Button
                disabled={api.busy}
                onClick={() => void api.run(draft.id, true).then(() => refresh(draft.id))}
                size="sm"
                variant="outline"
              >
                Simuler
              </Button>
              <Button
                disabled={api.busy || activeRun}
                onClick={() => void api.run(draft.id, false).then(() => refresh(draft.id))}
                size="sm"
              >
                {activeRun && <LoaderCircleIcon aria-hidden className="animate-spin" />}
                Indexer
              </Button>
            </div>
          </div>

          <Section
            title="Source et droits"
            description="Le groupe du fichier devient son ACL dans l’index. Un utilisateur ne récupère que ses groupes."
          >
            <div className="grid gap-4 lg:grid-cols-2">
              <Field htmlFor="source-name" label="Nom">
                <Input
                  id="source-name"
                  onChange={(event) => setDraft({ ...draft, name: event.target.value })}
                  value={draft.name}
                />
              </Field>
              <Field
                htmlFor="source-groups"
                label="Groupes"
                hint="Séparés par des virgules : public, gestion, sinistres…"
              >
                <Input
                  id="source-groups"
                  onChange={(event) =>
                    setDraft({
                      ...draft,
                      groups: event.target.value
                        .split(",")
                        .map((value) => value.trim())
                        .filter(Boolean),
                    })
                  }
                  value={draft.groups.join(", ")}
                />
              </Field>
            </div>
            <SwitchRow
              checked={draft.enabled}
              description="Une source désactivée conserve ses fichiers et son historique, mais refuse les nouveaux jobs."
              onCheckedChange={(enabled) => setDraft({ ...draft, enabled })}
              title="Source active"
            />
          </Section>

          <Section
            title="OCR par LLM"
            description="La couche texte du PDF reste prioritaire. Seules les pages scannées et les images sont envoyées au modèle choisi."
          >
            <SwitchRow
              checked={draft.ocr.enabled}
              icon={<ScanTextIcon className="size-4" />}
              onCheckedChange={(enabled) =>
                setDraft({ ...draft, ocr: { ...draft.ocr, enabled } })
              }
              title="Activer l’OCR visuel"
            />
            <div className="grid gap-4 lg:grid-cols-2">
              <Field htmlFor="ocr-provider" label="Provider">
                <Select
                  disabled={!draft.ocr.enabled}
                  onValueChange={(value) => {
                    const next = data.providers.find((item) => item.id === value)
                    setDraft({
                      ...draft,
                      ocr: {
                        ...draft.ocr,
                        provider: value as ProviderId,
                        model: next?.default_model ?? "",
                      },
                    })
                  }}
                  value={draft.ocr.provider}
                >
                  <SelectTrigger id="ocr-provider">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {data.providers.map((item) => (
                      <SelectItem key={item.id} value={item.id}>
                        {item.id}
                        {item.has_key ? "" : " — clé absente"}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
              <Field
                htmlFor="ocr-model"
                label="Modèle multimodal"
                hint="Nom libre : le catalogue aide, mais l’API n’impose aucun modèle."
              >
                <Input
                  disabled={!draft.ocr.enabled}
                  id="ocr-model"
                  list="ocr-models"
                  onChange={(event) =>
                    setDraft({
                      ...draft,
                      ocr: { ...draft.ocr, model: event.target.value },
                    })
                  }
                  placeholder={provider?.default_model ?? "nom du modèle"}
                  value={draft.ocr.model}
                />
                <datalist id="ocr-models">
                  {provider?.models.map((model) => <option key={model} value={model} />)}
                </datalist>
              </Field>
              <Field htmlFor="ocr-max-pages" label="Pages OCR maximum">
                <Input
                  disabled={!draft.ocr.enabled}
                  id="ocr-max-pages"
                  max={200}
                  min={1}
                  onChange={(event) =>
                    setDraft({
                      ...draft,
                      ocr: { ...draft.ocr, max_pages: Number(event.target.value) },
                    })
                  }
                  type="number"
                  value={draft.ocr.max_pages}
                />
              </Field>
              <Field htmlFor="ocr-dpi" label="Résolution">
                <Select
                  disabled={!draft.ocr.enabled}
                  onValueChange={(value) =>
                    setDraft({
                      ...draft,
                      ocr: { ...draft.ocr, dpi: Number(value) },
                    })
                  }
                  value={String(draft.ocr.dpi)}
                >
                  <SelectTrigger id="ocr-dpi">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="120">120 dpi — rapide</SelectItem>
                    <SelectItem value="160">160 dpi — équilibré</SelectItem>
                    <SelectItem value="220">220 dpi — petits caractères</SelectItem>
                    <SelectItem value="300">300 dpi — maximum</SelectItem>
                  </SelectContent>
                </Select>
              </Field>
            </div>
            <Field htmlFor="ocr-prompt" label="Instruction d’extraction">
              <textarea
                className="bg-input/50 focus-visible:border-ring focus-visible:ring-ring/30 min-h-28 w-full resize-y rounded-3xl border border-transparent px-3 py-2 text-sm outline-none focus-visible:ring-3 disabled:opacity-50"
                disabled={!draft.ocr.enabled}
                id="ocr-prompt"
                onChange={(event) =>
                  setDraft({
                    ...draft,
                    ocr: { ...draft.ocr, prompt: event.target.value },
                  })
                }
                value={draft.ocr.prompt}
              />
            </Field>
            {draft.ocr.enabled && provider && !provider.has_key && (
              <p className="text-destructive flex items-start gap-2 text-xs">
                <AlertTriangleIcon aria-hidden className="mt-0.5 size-3.5 shrink-0" />
                La clé de {provider.id} manque côté serveur. Elle n’est jamais stockée
                ni affichée dans cette interface.
              </p>
            )}
          </Section>

          <Section
            title="Fichiers"
            description={`Formats : ${data.supported_extensions.join(", ")} · maximum ${formatBytes(data.max_file_bytes)}.`}
          >
            <div className="grid gap-2 sm:grid-cols-[10rem_1fr_auto]">
              <Select onValueChange={setUploadGroup} value={uploadGroup}>
                <SelectTrigger aria-label="Groupe du fichier">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {draft.groups.map((group) => (
                    <SelectItem key={group} value={group}>
                      {group}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Input
                aria-label="Fichier à déposer"
                onChange={(event) => setUpload(event.target.files?.[0] ?? null)}
                type="file"
              />
              <Button
                disabled={!upload || api.busy}
                onClick={() => {
                  if (!upload) return
                  void api.upload(draft.id, uploadGroup, upload).then(async (value) => {
                    if (value) {
                      setUpload(null)
                      await api.loadDetails(draft.id)
                    }
                  })
                }}
                variant="outline"
              >
                <UploadIcon aria-hidden />
                Déposer
              </Button>
            </div>

            <ul className="divide-y border-y">
              {api.files.map((file) => (
                <li className="flex min-h-11 items-center gap-3 py-2" key={`${file.group}/${file.name}`}>
                  <FileTextIcon aria-hidden className="text-muted-foreground size-4" />
                  <span className="min-w-0 flex-1 truncate text-sm">{file.name}</span>
                  <span className="text-muted-foreground text-xs">{file.group}</span>
                  <span className="text-muted-foreground hidden text-xs sm:inline">
                    {formatBytes(file.size)}
                  </span>
                  <Button
                    aria-label={`Supprimer ${file.name}`}
                    onClick={() => {
                      if (!window.confirm(`Supprimer ${file.name} ?`)) return
                      void api
                        .deleteFile(draft.id, file.group, file.name)
                        .then(() => api.loadDetails(draft.id))
                    }}
                    size="icon-sm"
                    variant="ghost"
                  >
                    <Trash2Icon aria-hidden />
                  </Button>
                </li>
              ))}
            </ul>
          </Section>

          <Section
            title="Limites et synchronisation"
            description="La simulation parse et chiffre le lot sans vectoriser ni écrire l’index. L’OCR activé appelle toutefois le modèle et peut être facturé."
          >
            <Field htmlFor="max-chunks" label="Fragments maximum par exécution">
              <Input
                id="max-chunks"
                max={100000}
                min={1}
                onChange={(event) =>
                  setDraft({
                    ...draft,
                    options: {
                      ...draft.options,
                      max_chunks: Number(event.target.value),
                    },
                  })
                }
                type="number"
                value={draft.options.max_chunks}
              />
            </Field>
            <SwitchRow
              checked={draft.options.prune}
              description="Retire de l’index les fichiers supprimés de cette source uniquement."
              onCheckedChange={(prune) =>
                setDraft({
                  ...draft,
                  options: { ...draft.options, prune },
                })
              }
              title="Synchroniser les suppressions"
            />
          </Section>

          <div className="flex flex-wrap justify-between gap-2 border-t pt-4">
            <Button
              disabled={api.busy}
              onClick={() => {
                if (!window.confirm(`Supprimer la source « ${draft.name} » et son index ?`))
                  return
                void api.deleteSource(draft.id).then(async (value) => {
                  if (value !== undefined) {
                    setSelectedId(null)
                    await api.reload()
                  }
                })
              }}
              variant="ghost"
            >
              <Trash2Icon aria-hidden />
              Supprimer la source
            </Button>
            <Button
              disabled={
                api.busy ||
                !draft.name.trim() ||
                draft.groups.length === 0 ||
                (draft.ocr.enabled && !draft.ocr.model.trim())
              }
              onClick={() => void save()}
            >
              Enregistrer
            </Button>
          </div>

          <Section title="Exécutions" description="Historique durable des simulations et synchronisations.">
            <ol className="divide-y border-y">
              {api.runs.map((run) => (
                <li className="flex items-start gap-3 py-3 text-sm" key={run.id}>
                  {run.status === "succeeded" ? (
                    <CheckCircle2Icon aria-hidden className="text-primary mt-0.5 size-4" />
                  ) : run.status === "failed" ? (
                    <AlertTriangleIcon aria-hidden className="text-destructive mt-0.5 size-4" />
                  ) : (
                    <LoaderCircleIcon
                      aria-hidden
                      className="text-muted-foreground mt-0.5 size-4 animate-spin"
                    />
                  )}
                  <div className="min-w-0 flex-1">
                    <p className="font-medium">
                      {run.mode === "dry_run" ? "Simulation" : "Synchronisation"} ·{" "}
                      {statusLabel[run.status]}
                    </p>
                    {run.report && (
                      <p className="text-muted-foreground text-xs">
                        {run.report.indexed} indexé(s), {run.report.unchanged} inchangé(s),{" "}
                        {run.report.failed} erreur(s), {run.report.chunks} fragment(s)
                      </p>
                    )}
                    {run.error && (
                      <p className="text-destructive mt-1 break-words text-xs">{run.error}</p>
                    )}
                  </div>
                  <time className="text-muted-foreground text-xs" dateTime={run.created_at}>
                    {new Date(run.created_at).toLocaleString("fr-FR", {
                      day: "2-digit",
                      month: "2-digit",
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </time>
                </li>
              ))}
            </ol>
          </Section>
        </div>
      ) : null}
    </div>
  )
}
