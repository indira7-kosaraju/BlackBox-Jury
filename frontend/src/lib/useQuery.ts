import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";

export interface QueryState<T> {
  data: T | undefined;
  error: ApiError | null;
  loading: boolean;
  reload: () => void;
}

/**
 * Minimal data hook: loads on mount / when `deps` change, optional polling
 * (paused while the tab is hidden), never substitutes fabricated data on error.
 */
export function useQuery<T>(
  fn: (() => Promise<T>) | null,
  deps: unknown[],
  opts: { pollMs?: number | false } = {},
): QueryState<T> {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState<boolean>(fn !== null);
  const [tick, setTick] = useState(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    const current = fnRef.current;
    if (!current) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading((prev) => prev || data === undefined);
    current()
      .then((value) => {
        if (!cancelled) {
          setData(value);
          setError(null);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof ApiError ? err : new ApiError(0, "client_error", String(err)));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  useEffect(() => {
    const ms = opts.pollMs;
    if (!ms) return;
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") setTick((t) => t + 1);
    }, ms);
    return () => window.clearInterval(id);
  }, [opts.pollMs]);

  return { data, error, loading, reload };
}
