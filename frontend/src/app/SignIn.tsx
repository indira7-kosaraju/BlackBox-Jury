import { KeyRound, PlayCircle } from "lucide-react";
import { useState, type FormEvent } from "react";
import { ApiError } from "../api/client";
import { BrandMark, ErrorState } from "../components/ui";
import { useSession } from "./session";

export function SignIn() {
  const { signInLive, startDemo, signOut } = useSession();
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const api = signInLive(token.trim());
    try {
      await api.ready(); // authenticated call: verifies the token before entering
    } catch (err) {
      signOut();
      setError(err instanceof ApiError ? err : new ApiError(0, "client_error", String(err)));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="gate">
      <div className="panel gate-card">
        <div className="row">
          <BrandMark size={40} />
          <div className="stack" style={{ gap: 2 }}>
            <span className="brand-name" style={{ fontSize: 18 }}>
              BLACKBOX JURY
            </span>
            <span className="eyebrow">Trust nothing. Verify everything.</span>
          </div>
        </div>
        <p className="muted">
          Connect to your local Blackbox Jury server. Paste the <code className="mono">BBJ_API_TOKEN</code> from your{" "}
          <code className="mono">.env</code>. It is kept only in this browser tab and cleared when you close it.
        </p>
        <form className="stack" onSubmit={submit}>
          <label className="field">
            <span className="label">API token</span>
            <input
              className="input mono"
              type="password"
              autoComplete="off"
              spellCheck={false}
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="Paste token"
              required
              minLength={32}
            />
          </label>
          <button className="btn btn--primary" type="submit" disabled={busy || token.trim().length < 32}>
            <KeyRound size={16} aria-hidden /> {busy ? "Connecting…" : "Connect to live server"}
          </button>
        </form>
        {error ? <ErrorState error={error} /> : null}
        <div className="divider" />
        <div className="stack" style={{ gap: 8 }}>
          <button className="btn" onClick={() => void startDemo()}>
            <PlayCircle size={16} aria-hidden /> Explore demo data (recorded run)
          </button>
          <span className="faint" style={{ fontSize: 12 }}>
            Demo mode replays a run recorded from the real sandbox. Every screen is labelled DEMO DATA.
          </span>
        </div>
      </div>
    </div>
  );
}
