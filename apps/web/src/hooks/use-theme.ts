import { useCallback, useSyncExternalStore } from "react";

const listeners = new Set<() => void>();
const notify = () => listeners.forEach((listener) => listener());

const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => listeners.delete(listener);
};

const isDark = () => document.documentElement.classList.contains("dark");

/**
 * Le thème initial est déjà posé par le script inline de index.html (anti-flash) ;
 * ce hook ne fait que le lire et le basculer.
 */
export function useTheme() {
  const dark = useSyncExternalStore(subscribe, isDark, () => false);

  const toggle = useCallback(() => {
    const next = !isDark();
    document.documentElement.classList.toggle("dark", next);
    localStorage.setItem("theme", next ? "dark" : "light");
    notify();
  }, []);

  return { dark, toggle };
}
