/**
 * Mémoire locale du modèle choisi, par provider.
 *
 * La configuration de l'agent est **globale et serveur** (cf. docs/settings.md) :
 * ce store n'est donc pas une source de vérité, seulement un souvenir de
 * navigateur. Il sert à une chose : quand on change de provider, réappliquer le
 * dernier modèle qu'on y avait choisi plutôt que retomber sur son défaut.
 *
 * Un seul point d'écriture — `patchModel()` dans use-settings.ts — pour que le
 * souvenir suive toute mutation du modèle, qu'elle vienne du composer ou du
 * panneau de configuration.
 */

const KEY = "model-by-provider";

function read(): Record<string, string> {
  try {
    const raw = localStorage.getItem(KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : null;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    // Une entrée corrompue (édition manuelle, format d'une version antérieure) ne
    // doit pas casser le sélecteur : on ne garde que les paires exploitables.
    return Object.fromEntries(
      Object.entries(parsed as Record<string, unknown>).filter(
        (entry): entry is [string, string] =>
          typeof entry[1] === "string" && entry[1].length > 0,
      ),
    );
  } catch {
    // localStorage inaccessible (navigation privée, quota) : on tourne sans mémoire.
    return {};
  }
}

export function recallModel(provider: string): string | undefined {
  return read()[provider];
}

export function rememberModel(provider: string, model: string): void {
  try {
    localStorage.setItem(KEY, JSON.stringify({ ...read(), [provider]: model }));
  } catch {
    // Idem : pas de mémoire, mais surtout pas d'exception dans un gestionnaire d'UI.
  }
}
