/**
 * Session: which data source the UI is using (live backend or recorded demo)
 * and the typed API bound to it. The token lives only in sessionStorage.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, LiveTransport, type Api, type Transport } from "../api/client";

const TOKEN_KEY = "bbj.token";
const MODE_KEY = "bbj.mode";

interface SessionValue {
  api: Api | null;
  mode: "live" | "demo" | null;
  /** True while a demo session is being restored after a reload. */
  restoring: boolean;
  signInLive: (token: string) => Api;
  startDemo: () => Promise<void>;
  signOut: () => void;
}

const SessionContext = createContext<SessionValue | null>(null);

function readStorage(key: string): string | null {
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}
function writeStorage(key: string, value: string | null): void {
  try {
    if (value === null) sessionStorage.removeItem(key);
    else sessionStorage.setItem(key, value);
  } catch {
    /* storage unavailable (private mode): session simply won't persist */
  }
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [transport, setTransport] = useState<Transport | null>(() => {
    const token = readStorage(TOKEN_KEY);
    return token && readStorage(MODE_KEY) === "live" ? new LiveTransport(token) : null;
  });

  const [restoring, setRestoring] = useState(() => readStorage(MODE_KEY) === "demo");

  const signInLive = useCallback((token: string) => {
    const t = new LiveTransport(token);
    writeStorage(TOKEN_KEY, token);
    writeStorage(MODE_KEY, "live");
    setTransport(t);
    return api(t);
  }, []);

  const startDemo = useCallback(async () => {
    const { DemoTransport } = await import("../demo/transport");
    writeStorage(TOKEN_KEY, null);
    writeStorage(MODE_KEY, "demo");
    setTransport(new DemoTransport());
  }, []);

  useEffect(() => {
    if (!restoring) return;
    startDemo().finally(() => setRestoring(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const signOut = useCallback(() => {
    writeStorage(TOKEN_KEY, null);
    writeStorage(MODE_KEY, null);
    setTransport(null);
  }, []);

  const value = useMemo<SessionValue>(
    () => ({ api: transport ? api(transport) : null, mode: transport?.mode ?? null, restoring, signInLive, startDemo, signOut }),
    [transport, restoring, signInLive, startDemo, signOut],
  );
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionValue {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession outside SessionProvider");
  return ctx;
}

export function useApi(): Api {
  const { api: a } = useSession();
  if (!a) throw new Error("useApi before sign-in");
  return a;
}
