import { useCallback, useEffect, useState } from "react";

const read = (fallback: string) => window.location.hash.replace(/^#\/?/, "") || fallback;

/** Tiny hash router: "#/scores" -> "scores". No dependency needed. */
export function useHashRoute(fallback: string): [string, (route: string) => void] {
  const [route, setRoute] = useState(() => read(fallback));
  useEffect(() => {
    const onChange = () => setRoute(read(fallback));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, [fallback]);
  const navigate = useCallback((r: string) => {
    window.location.hash = `/${r}`;
  }, []);
  return [route, navigate];
}
