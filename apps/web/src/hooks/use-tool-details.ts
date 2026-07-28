import { useCallback, useSyncExternalStore } from "react";

/**
 * Préférence d'affichage — purement locale au navigateur.
 *
 * Montrer les paramètres et le résultat brut (JSON) d'un appel d'outil est une
 * vue « développeur » : inutile, voire intimidante, pour un utilisateur non
 * technique. Par défaut on affiche donc une simple ligne d'activité lisible ;
 * ce réglage révèle les détails techniques.
 *
 * C'est une préférence d'interface, pas un réglage de l'agent : le backend n'a
 * aucune raison de la connaître (elle ne change ni le modèle, ni les outils, ni
 * ce qui est envoyé). Elle vit donc en `localStorage`, comme le thème — même
 * motif `useSyncExternalStore` pour que tous les appels d'outil et le panneau de
 * configuration restent synchronisés dans l'onglet courant.
 */

const KEY = "tool-details";

const listeners = new Set<() => void>();
const notify = () => listeners.forEach((listener) => listener());

const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => listeners.delete(listener);
};

const read = (): boolean => {
  try {
    return localStorage.getItem(KEY) === "1";
  } catch {
    // localStorage inaccessible (navigation privée, quota) : vue propre par défaut.
    return false;
  }
};

export function useToolDetails() {
  const enabled = useSyncExternalStore(subscribe, read, () => false);

  const setEnabled = useCallback((next: boolean) => {
    try {
      localStorage.setItem(KEY, next ? "1" : "0");
    } catch {
      // Pas de persistance possible, mais surtout jamais d'exception dans l'UI.
    }
    notify();
  }, []);

  return { enabled, setEnabled };
}
