// Runtime connection settings. The platform bearer token is entered by the user in Settings and kept in
// sessionStorage for this tab only. Provider credentials (e.g. NEBIUS_API_KEY) never belong in the browser.
import { useSyncExternalStore } from "react";

export interface ConnectionSettings {
  apiBase: string;
  token: string;
}

const KEY = "agentforge.connection";
export const DEMO_MODE = import.meta.env.VITE_DEMO_MODE === "1";

function read(): ConnectionSettings {
  const fallback = { apiBase: import.meta.env.VITE_API_BASE_URL || "/api/v1", token: "" };
  try {
    const raw = sessionStorage.getItem(KEY);
    return raw ? { ...fallback, ...(JSON.parse(raw) as Partial<ConnectionSettings>) } : fallback;
  } catch {
    return fallback;
  }
}

let current = read();
const listeners = new Set<() => void>();

export function getConnection(): ConnectionSettings {
  return current;
}

export function setConnection(next: Partial<ConnectionSettings>): void {
  current = { ...current, ...next, apiBase: (next.apiBase ?? current.apiBase).replace(/\/+$/, "") };
  try {
    sessionStorage.setItem(KEY, JSON.stringify(current));
  } catch {
    /* storage unavailable: settings stay in memory for this tab */
  }
  listeners.forEach((l) => l());
}

export function useConnection(): ConnectionSettings {
  return useSyncExternalStore(
    (cb) => (listeners.add(cb), () => listeners.delete(cb)),
    () => current,
  );
}
