/**
 * Miroir du contrat de `GET /api/settings` (apps/api/src/agent/settings.py).
 *
 * Les noms de champs restent en snake_case : ils viennent tels quels de l'API,
 * les renommer côté client n'apporterait qu'une couche de traduction à maintenir.
 */

export type ProviderId = "groq" | "google" | "ollama" | "openai"
export type McpTransport = "stdio" | "http" | "sse"
/** `default` = aucun palier transmis, le modèle garde son comportement natif. */
export type EffortLevel = "default" | "low" | "medium" | "high"

export type ToolSetting = {
  name: string
  description: string
  enabled: boolean
}

export type ProviderInfo = {
  id: ProviderId
  default_model: string | null
  /**
   * Catalogue indicatif proposé par l'API (`agent.model.PROVIDER_MODELS`), jamais
   * exhaustif : la surcharge libre du panneau accepte n'importe quel nom.
   */
  models: string[]
  requires_key: boolean
  /** Seule information exposée sur les clés API : jamais leur valeur. */
  has_key: boolean
}

export type AgentSettings = {
  /** null = le prompt par défaut du graphe est utilisé. */
  system_prompt: string | null
  max_tool_loops: number
  temperature: number
  /** Bornes fournies par l'API : le front affiche exactement ce qu'elle valide. */
  max_tool_loops_range: [number, number]
  temperature_range: [number, number]
  /** Plafond au-delà duquel le serveur rogne l'historique (cf. graph.MAX_CONTEXT_TOKENS). */
  context_window_tokens: number
}

export type ModelSettings = {
  provider: ProviderId
  /** null = modèle par défaut du provider. */
  model: string | null
  effective_model: string | null
  reasoning_effort: EffortLevel
  /**
   * Paliers acceptés par le modèle **actif**, calculés par l'API. Vide = ce modèle
   * n'expose pas d'effort de raisonnement : le réglage est alors désactivé, plutôt
   * que d'envoyer un paramètre que le provider refuserait.
   */
  effort_levels: EffortLevel[]
  providers: ProviderInfo[]
}

/**
 * État de la dernière découverte des outils d'un serveur MCP, calculé par l'API.
 *
 *   ready   → `tools` outils bindés au modèle
 *   error   → injoignable, `error` porte la cause ; ses outils sont absents
 *   idle    → serveur désactivé
 *   unknown → activé mais pas encore découvert
 */
export type McpStatus = {
  state: "ready" | "error" | "idle" | "unknown"
  tools: number
  error: string | null
}

export type McpServer = {
  id: string
  name: string
  transport: McpTransport
  url: string | null
  command: string | null
  args: string[]
  env: Record<string, string>
  enabled: boolean
  status?: McpStatus
}

export type SettingsPayload = {
  /** false = Postgres injoignable : on lit les défauts, rien ne s'enregistre. */
  persisted: boolean
  agent: AgentSettings
  model: ModelSettings
  tools: ToolSetting[]
  mcp_servers: McpServer[]
}

export type McpServerInput = {
  name: string
  transport: McpTransport
  url?: string | null
  command?: string | null
  args?: string[]
  env?: Record<string, string>
  enabled?: boolean
}
