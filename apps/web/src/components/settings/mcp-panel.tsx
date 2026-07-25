import { useState } from "react"
import { PlusIcon, ServerIcon, Trash2Icon, TriangleAlertIcon } from "lucide-react"
import { Field, Section, SwitchRow } from "@/components/settings/controls"
import type { McpServer, McpServerInput, McpTransport } from "@/components/settings/types"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { cn } from "@/lib/utils"

const TRANSPORTS: { id: McpTransport; label: string }[] = [
  { id: "stdio", label: "stdio — processus local" },
  { id: "http", label: "http — serveur distant" },
  { id: "sse", label: "sse — serveur distant (flux)" },
]

const EMPTY: Draft = { name: "", transport: "stdio", url: "", command: "", args: "", env: "" }

type Draft = {
  name: string
  transport: McpTransport
  url: string
  command: string
  /** Saisis séparés par des espaces, comme sur une ligne de commande. */
  args: string
  /** Une variable par ligne, au format `CLE=valeur`. */
  env: string
}

function parseEnv(raw: string): Record<string, string> {
  return Object.fromEntries(
    raw
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line.length > 0 && line.includes("="))
      .map((line) => {
        const index = line.indexOf("=")
        return [line.slice(0, index).trim(), line.slice(index + 1).trim()]
      }),
  )
}

function toInput(draft: Draft): McpServerInput {
  const stdio = draft.transport === "stdio"
  return {
    name: draft.name.trim(),
    transport: draft.transport,
    url: stdio ? null : draft.url.trim(),
    command: stdio ? draft.command.trim() : null,
    args: stdio ? draft.args.split(/\s+/).filter(Boolean) : [],
    env: parseEnv(draft.env),
  }
}

export function McpPanel({
  servers,
  disabled,
  saving,
  onCreate,
  onToggle,
  onDelete,
}: {
  servers: McpServer[]
  disabled: boolean
  saving: boolean
  onCreate: (body: McpServerInput) => Promise<boolean>
  onToggle: (id: string, enabled: boolean) => void
  onDelete: (id: string) => void
}) {
  const [draft, setDraft] = useState<Draft>(EMPTY)
  const stdio = draft.transport === "stdio"

  const complete =
    draft.name.trim().length > 0 &&
    (stdio ? draft.command.trim().length > 0 : draft.url.trim().length > 0)

  const submit = async () => {
    if (await onCreate(toInput(draft))) setDraft(EMPTY)
  }

  return (
    <div className="flex flex-col gap-6">
      <p className="text-muted-foreground flex items-start gap-2 rounded-3xl border border-dashed px-3 py-2 text-xs">
        <TriangleAlertIcon aria-hidden className="mt-0.5 size-3.5 shrink-0" />
        <span>
          Les outils d'un serveur activé sont <strong>découverts et bindés au modèle</strong>{" "}
          à l'enregistrement. Un serveur injoignable est signalé ici et ignoré — il ne
          bloque ni le démarrage ni le chat.
        </span>
      </p>

      <Section title={`Serveurs enregistrés (${servers.length})`}>
        {servers.length === 0 ? (
          <p className="text-muted-foreground rounded-3xl border border-dashed px-3 py-4 text-center text-xs">
            Aucun serveur MCP.
          </p>
        ) : (
          <ul className="flex flex-col gap-2">
            {servers.map((server) => (
              <li className="flex items-center gap-2" key={server.id}>
                <SwitchRow
                  checked={server.enabled}
                  description={
                    <>
                      <span className="font-mono break-all">
                        {server.transport === "stdio"
                          ? [server.command, ...server.args].join(" ")
                          : server.url}
                      </span>
                      {/* L'état réel de la dernière découverte, pas une promesse. */}
                      {server.enabled && server.status && (
                        <span
                          className={cn(
                            "mt-0.5 block",
                            server.status.state === "error" && "text-destructive",
                          )}
                        >
                          {server.status.state === "ready" &&
                            `${server.status.tools} outil${server.status.tools > 1 ? "s" : ""} branché${server.status.tools > 1 ? "s" : ""}`}
                          {server.status.state === "error" &&
                            `injoignable — ${server.status.error ?? "cause inconnue"}`}
                          {server.status.state === "unknown" && "découverte en attente"}
                        </span>
                      )}
                    </>
                  }
                  disabled={disabled}
                  icon={<ServerIcon className="size-4" />}
                  onCheckedChange={(next) => onToggle(server.id, next)}
                  title={`${server.name} · ${server.transport}`}
                />
                <Button
                  aria-label={`Supprimer ${server.name}`}
                  className="shrink-0 pointer-coarse:size-11"
                  disabled={disabled || saving}
                  onClick={() => onDelete(server.id)}
                  size="icon-sm"
                  variant="destructive"
                >
                  <Trash2Icon />
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Ajouter un serveur">
        {/* auto-fit : une colonne en mobile, deux dès ~24rem de large. */}
        <div className="grid gap-4 [grid-template-columns:repeat(auto-fit,minmax(11rem,1fr))]">
          <Field htmlFor="mcp-name" label="Nom">
            <Input
              className="pointer-coarse:h-11"
              disabled={disabled}
              id="mcp-name"
              onChange={(event) => setDraft({ ...draft, name: event.target.value })}
              placeholder="Filesystem"
              value={draft.name}
            />
          </Field>

          <Field htmlFor="mcp-transport" label="Transport">
            <Select
              disabled={disabled}
              onValueChange={(value) =>
                setDraft({ ...draft, transport: value as McpTransport })
              }
              value={draft.transport}
            >
              <SelectTrigger className="pointer-coarse:h-11" id="mcp-transport">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TRANSPORTS.map((transport) => (
                  <SelectItem key={transport.id} value={transport.id}>
                    {transport.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
        </div>

        {stdio ? (
          <div className="grid gap-4 [grid-template-columns:repeat(auto-fit,minmax(11rem,1fr))]">
            <Field htmlFor="mcp-command" label="Commande">
              <Input
                className="pointer-coarse:h-11"
                disabled={disabled}
                id="mcp-command"
                onChange={(event) => setDraft({ ...draft, command: event.target.value })}
                placeholder="npx"
                value={draft.command}
              />
            </Field>
            <Field
              htmlFor="mcp-args"
              label="Arguments"
              hint="Séparés par des espaces."
            >
              <Input
                className="pointer-coarse:h-11"
                disabled={disabled}
                id="mcp-args"
                onChange={(event) => setDraft({ ...draft, args: event.target.value })}
                placeholder="-y @modelcontextprotocol/server-filesystem /tmp"
                value={draft.args}
              />
            </Field>
          </div>
        ) : (
          <Field htmlFor="mcp-url" label="URL">
            <Input
              className="pointer-coarse:h-11"
              disabled={disabled}
              id="mcp-url"
              onChange={(event) => setDraft({ ...draft, url: event.target.value })}
              placeholder="https://exemple.tld/mcp"
              type="url"
              value={draft.url}
            />
          </Field>
        )}

        <Field
          htmlFor="mcp-env"
          label="Variables d'environnement"
          hint="Une par ligne, au format CLE=valeur. N'y mettez pas de secret que vous ne voulez pas voir en base."
        >
          <textarea
            className="bg-input/50 placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/30 min-h-20 w-full resize-y rounded-3xl border border-transparent px-3.5 py-2.5 font-mono text-base outline-none focus-visible:ring-3 disabled:opacity-50 md:text-sm"
            disabled={disabled}
            id="mcp-env"
            onChange={(event) => setDraft({ ...draft, env: event.target.value })}
            placeholder={"NODE_ENV=production\nTOKEN=xxx"}
            value={draft.env}
          />
        </Field>

        <Button
          className="self-start pointer-coarse:min-h-11"
          disabled={disabled || saving || !complete}
          onClick={submit}
        >
          <PlusIcon />
          Ajouter
        </Button>
      </Section>
    </div>
  )
}
