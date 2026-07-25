import { useEffect, useState } from "react";

export type Health = {
  status: "ok";
  provider: string;
  tools: string[];
  /** false quand Postgres est injoignable : le chat marche, sans historique. */
  history: boolean;
};

export type HealthState =
  | { state: "loading" }
  | { state: "online"; data: Health }
  | { state: "offline"; error: string };

/**
 * Ping du backend au montage : on préfère afficher « backend injoignable »
 * tout de suite plutôt que de laisser l'utilisateur découvrir le problème
 * après avoir tapé son premier message.
 */
export function useHealth(): HealthState {
  const [health, setHealth] = useState<HealthState>({ state: "loading" });

  useEffect(() => {
    const controller = new AbortController();

    fetch("/api/health", { signal: controller.signal })
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(`HTTP ${res.status}`))))
      .then((data: Health) => setHealth({ state: "online", data }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setHealth({ state: "offline", error: error instanceof Error ? error.message : "inconnu" });
      });

    return () => controller.abort();
  }, []);

  return health;
}
