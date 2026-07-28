/**
 * Les deux seules URLs de l'application.
 *
 *   /ichat          → nouvelle conversation
 *   /ichat/c/:id    → conversation existante
 *
 * Pas de routeur : à deux routes, `history.pushState` + `popstate` suffisent, et
 * ajouter react-router ou tanstack-router pour ça alourdirait le bundle sans rien
 * apporter. Le jour où il faut des routes imbriquées, des loaders ou du code
 * splitting par route, c'est le moment de prendre un vrai routeur — pas avant.
 *
 * Tout est ici pour qu'il n'y ait qu'un endroit à toucher si la base change.
 */

export const CHAT_BASE = "/ichat";

/**
 * URL canonique de l'écran de connexion. Symétrique de `CHAT_BASE` : l'app
 * authentifiée vit sur `/ichat`, la connexion sur `/login`. Un utilisateur anonyme
 * y est ramené quelle que soit l'URL demandée (cf. `auth-context`), pour que le
 * login ait toujours la même adresse — et que l'autofill de démo, qui dépend de
 * l'URL, s'y déclenche.
 */
export const LOGIN_PATH = "/login";

const THREAD_SEGMENT = `${CHAT_BASE}/c/`;

/** L'id de conversation porté par l'URL, s'il y en a un. */
export function threadIdFromPath(pathname: string): string | undefined {
  if (!pathname.startsWith(THREAD_SEGMENT)) return undefined;
  // Un seul segment après /c/ : `/ichat/c/a/b` n'est pas une conversation.
  const rest = pathname.slice(THREAD_SEGMENT.length);
  if (!rest || rest.includes("/")) return undefined;
  // Les ids voyagent encodés dans l'URL (cf. `pathForThread`).
  return decodeURIComponent(rest);
}

/** URL canonique d'une conversation, ou de l'écran « nouvelle conversation ». */
export function pathForThread(threadId?: string): string {
  return threadId ? `${THREAD_SEGMENT}${encodeURIComponent(threadId)}` : CHAT_BASE;
}

/** L'URL appartient-elle à l'application ? Sinon on redirige vers `CHAT_BASE`. */
export function isChatPath(pathname: string): boolean {
  // `/ichat/` (barre finale) est toléré : c'est la même page, pas une 404.
  if (pathname === CHAT_BASE || pathname === `${CHAT_BASE}/`) return true;
  return threadIdFromPath(pathname) !== undefined;
}
