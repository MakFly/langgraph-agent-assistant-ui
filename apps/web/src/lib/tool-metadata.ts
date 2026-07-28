import {
  BookText,
  Calculator,
  CloudSun,
  FileSearch,
  Newspaper,
  WrenchIcon,
  type LucideIcon,
} from "lucide-react"

/**
 * Source de vérité unique des métadonnées d'outils, partagée par l'affichage
 * dans le fil de conversation (`tool-fallback`), la sidebar et le panneau de
 * réglages. Avant, chaque écran redéclarait son propre couple icône/label ; le
 * commentaire de `tools-panel` avouait même « Même correspondance que la
 * sidebar ». Un seul endroit = plus de divergence quand un outil est ajouté.
 *
 * `label`    : nom humain de la source (affiché en gras).
 * `action`   : formulation à l'infinitif pour l'état « en cours » (shimmer).
 * `describe` : aperçu lisible de l'appel — l'argument principal (la requête),
 *              complété du **résultat** dès qu'il est disponible. C'est ce que
 *              voit un utilisateur non technique : « 2340 * 0.18 = 421,2 »
 *              plutôt qu'un JSON. Reçoit les args parsés (jamais null : `{}` si
 *              illisible pendant le streaming) et le résultat déjà coercé en
 *              objet ; renvoie `null` quand il n'y a encore rien à montrer.
 */
export type ToolMeta = {
  icon: LucideIcon
  label: string
  action: string
  describe?: (args: Record<string, unknown>, result: unknown) => string | null
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)

const asText = (value: unknown): string | null => {
  if (typeof value === "string") return value.trim() || null
  if (typeof value === "number" || typeof value === "boolean") return String(value)
  return null
}

/** Nombre lisible côté humain (séparateurs FR), sinon repli texte. */
const formatNumber = (value: unknown): string | null => {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value.toLocaleString("fr-FR", { maximumFractionDigits: 6 })
  }
  return asText(value)
}

/** « 0 résultat », « 1 extrait », « 5 résultats » — accord du pluriel. */
const countLabel = (n: number, word: string): string =>
  `${n} ${word}${n > 1 ? "s" : ""}`

/** Erreur renvoyée par un outil (`{ "error": "..." }`), le cas échéant. */
const resultError = (result: unknown): string | null =>
  isRecord(result) ? asText(result.error) : null

export const TOOL_META: Record<string, ToolMeta> = {
  wikipedia_search: {
    icon: BookText,
    label: "Wikipédia",
    action: "Recherche sur Wikipédia",
    describe: (args, result) => {
      const query = asText(args.query)
      const err = resultError(result)
      if (err) return query ? `${query} — ${err}` : err
      const list = isRecord(result) && Array.isArray(result.results) ? result.results : null
      return query && list ? `${query} — ${countLabel(list.length, "résultat")}` : query
    },
  },
  hacker_news_search: {
    icon: Newspaper,
    label: "Hacker News",
    action: "Recherche sur Hacker News",
    describe: (args, result) => {
      const query = asText(args.query)
      const err = resultError(result)
      if (err) return query ? `${query} — ${err}` : err
      const list = isRecord(result) && Array.isArray(result.stories) ? result.stories : null
      return query && list ? `${query} — ${countLabel(list.length, "résultat")}` : query
    },
  },
  weather_forecast: {
    icon: CloudSun,
    label: "Météo",
    action: "Consultation de la météo",
    describe: (args, result) => {
      const city = asText(args.city)
      const err = resultError(result)
      if (err) return err
      const current = isRecord(result) && isRecord(result.current) ? result.current : null
      const reading = current
        ? [asText(current.temperature), asText(current.conditions)].filter(Boolean).join(", ")
        : ""
      return city && reading ? `${city} — ${reading}` : city
    },
  },
  calculator: {
    icon: Calculator,
    label: "Calculatrice",
    action: "Calcul en cours",
    describe: (args, result) => {
      const expression = asText(args.expression)
      const err = resultError(result)
      if (err) return expression ? `${expression} — ${err}` : err
      const value = isRecord(result) ? formatNumber(result.result) : null
      return expression && value ? `${expression} = ${value}` : expression
    },
  },
  document_search: {
    icon: FileSearch,
    label: "Documents internes",
    action: "Recherche documentaire",
    describe: (args, result) => {
      const query = asText(args.query)
      const err = resultError(result)
      if (err) return query ? `${query} — ${err}` : err
      const list = isRecord(result) && Array.isArray(result.results) ? result.results : null
      return query && list ? `${query} — ${countLabel(list.length, "extrait")}` : query
    },
  },
}

/** snake_case → « Snake Case » pour un outil inconnu (MCP, ajout futur). */
const prettifyToolName = (name: string): string =>
  name
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b\w/g, (c) => c.toUpperCase()) || name

/** Premier argument textuel non vide — aperçu générique pour outil inconnu. */
const firstTextArg = (args: Record<string, unknown>): string | null => {
  for (const value of Object.values(args)) {
    const text = asText(value)
    if (text) return text
  }
  return null
}

/**
 * Résout un nom d'outil vers ses métadonnées, avec un repli sûr pour tout
 * outil non déclaré : rien ne « disparaît » de l'affichage.
 */
export const resolveToolMeta = (name: string): ToolMeta =>
  TOOL_META[name] ?? {
    icon: WrenchIcon,
    label: prettifyToolName(name),
    action: prettifyToolName(name),
    describe: (args) => firstTextArg(args),
  }

/** Args bruts (JSON, possiblement partiel en cours de streaming) → objet sûr. */
const parseArgs = (argsText: string | undefined): Record<string, unknown> => {
  if (!argsText) return {}
  try {
    const parsed: unknown = JSON.parse(argsText)
    return isRecord(parsed) ? parsed : {}
  } catch {
    return {}
  }
}

/** Résultat d'outil (chaîne JSON via `tool_json`, ou déjà objet) → valeur usable. */
const coerceResult = (result: unknown): unknown => {
  if (typeof result === "string") {
    try {
      return JSON.parse(result)
    } catch {
      return result
    }
  }
  return result
}

/**
 * Aperçu court et lisible d'un appel d'outil : la requête, complétée du résultat
 * dès qu'il est là. Pendant le streaming les args sont un JSON partiel et il n'y
 * a pas encore de résultat — on renvoie au mieux la requête, sinon `null`.
 */
export const summarizeToolCall = (
  name: string,
  argsText: string | undefined,
  result: unknown,
): string | null => {
  const meta = resolveToolMeta(name)
  return meta.describe?.(parseArgs(argsText), coerceResult(result)) ?? null
}
