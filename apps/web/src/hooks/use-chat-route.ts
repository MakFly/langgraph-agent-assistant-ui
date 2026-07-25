import { useCallback, useEffect, useState } from "react";
import {
  CHAT_BASE,
  isChatPath,
  pathForThread,
  threadIdFromPath,
} from "@/lib/chat-route";

/**
 * L'URL comme unique source de vérité de la conversation ouverte.
 *
 * `useRemoteThreadListRuntime` accepte déjà un `threadId` contrôlé et un
 * `onThreadIdChange` : brancher l'un sur l'autre suffit, il n'y a pas à piloter le
 * runtime à la main. Conséquences gratuites : lien profond, rechargement, boutons
 * précédent/suivant du navigateur.
 */
export function useChatRoute() {
  const [pathname, setPathname] = useState(() => window.location.pathname);

  // Précédent / suivant du navigateur : seul `popstate` nous prévient.
  useEffect(() => {
    const onPopState = () => setPathname(window.location.pathname);
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  // Toute autre URL — « / » comprise — atterrit sur /ichat. `replaceState` et non
  // `pushState` : sinon un retour arrière depuis /ichat reviendrait sur « / », qui
  // redirigerait de nouveau, et le bouton « précédent » serait piégé.
  useEffect(() => {
    if (isChatPath(pathname)) return;
    window.history.replaceState(null, "", CHAT_BASE);
    setPathname(CHAT_BASE);
  }, [pathname]);

  /** Ouvre une conversation, ou l'écran « nouvelle conversation » sans argument. */
  const openThread = useCallback((threadId?: string) => {
    const target = pathForThread(threadId);
    // Le runtime rappelle parfois avec l'id déjà affiché (fin d'initialisation
    // d'une conversation) : sans cette garde, l'historique se remplirait de
    // doublons et le bouton « précédent » ne bougerait plus.
    if (window.location.pathname !== target) {
      window.history.pushState(null, "", target);
    }
    setPathname(target);
  }, []);

  return { threadId: threadIdFromPath(pathname), openThread };
}
