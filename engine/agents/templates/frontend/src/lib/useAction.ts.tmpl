import { useCallback, useRef, useState } from "react";
import { ApiError } from "../api/generated";

export { useAsync } from "./useAsync"; // one import site for both hooks

/** Readable message for anything thrown by an api.* call (no casts needed at the call site). */
export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) return e.status === 0 ? "Cannot reach the server. Check your connection." : e.message;
  return e instanceof Error ? e.message : String(e);
}

/** HTTP status of an ApiError (0 = network failure), or undefined for other errors. */
export function errorStatus(e: unknown): number | undefined {
  return e instanceof ApiError ? e.status : undefined;
}

export interface ActionState<A extends unknown[], T> {
  run: (...args: A) => Promise<T | undefined>; // resolves undefined when the action failed (see error)
  pending: boolean;
  error: string | null; // ready to render: {error && <Alert>{error}</Alert>}
  status: number | undefined; // e.g. 401 -> ask the user to log in
  done: boolean; // true after the last run succeeded
  reset: () => void;
}

/** For form submits and button actions (mutations). Use useAsync for data that loads on mount. */
export function useAction<A extends unknown[], T>(fn: (...args: A) => Promise<T>): ActionState<A, T> {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<number | undefined>(undefined);
  const [done, setDone] = useState(false);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const run = useCallback(async (...args: A): Promise<T | undefined> => {
    setPending(true);
    setError(null);
    setStatus(undefined);
    setDone(false);
    try {
      const out = await fnRef.current(...args);
      setDone(true);
      return out;
    } catch (e: unknown) {
      setError(errorMessage(e));
      setStatus(errorStatus(e));
      return undefined;
    } finally {
      setPending(false);
    }
  }, []);
  const reset = useCallback(() => { setError(null); setStatus(undefined); setDone(false); }, []);
  return { run, pending, error, status, done, reset };
}
