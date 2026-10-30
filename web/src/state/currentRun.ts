// The run the user last opened, so run-scoped nav items (Files, Reviews, ...) know where to go.
import { useSyncExternalStore } from "react";

const KEY = "agentforge.currentRun";
let current: string | null = (() => {
  try {
    return sessionStorage.getItem(KEY);
  } catch {
    return null;
  }
})();
const listeners = new Set<() => void>();

export function setCurrentRun(id: string | null): void {
  if (id === current) return;
  current = id;
  try {
    if (id) sessionStorage.setItem(KEY, id);
    else sessionStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
  listeners.forEach((l) => l());
}

export const useCurrentRun = () =>
  useSyncExternalStore((cb) => (listeners.add(cb), () => listeners.delete(cb)), () => current);
