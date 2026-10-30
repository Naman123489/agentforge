import { useEffect, useRef, useState } from "react";

export interface AsyncState<T> {
  data: T | undefined;
  error: Error | null;
  loading: boolean;
  reload: () => void;
}

/** Runs `load` on mount and when `key` changes; exposes loading / error / data for honest UI states. */
export function useAsync<T>(load: () => Promise<T>, key: string): AsyncState<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);
  const loader = useRef(load);
  loader.current = load;
  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    loader.current()
      .then((d) => { if (alive) setData(d); })
      .catch((e: unknown) => { if (alive) setError(e instanceof Error ? e : new Error(String(e))); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [key, nonce]);
  return { data, error, loading, reload: () => setNonce((n) => n + 1) };
}
