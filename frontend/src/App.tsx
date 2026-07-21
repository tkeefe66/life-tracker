import { useEffect, useState } from "react";
import { apiGet, login, UnauthorizedError } from "./api";

type Tab = "today" | "scorecard" | "settings";

function LoginScreen({ onLogin }: { onLogin: () => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      if (await login(password)) onLogin();
      else setError("Wrong password");
    } catch {
      setError("Can't reach the server.");
    }
  };
  return (
    <form className="login" onSubmit={submit}>
      <h1>On Track</h1>
      <input
        type="password"
        value={password}
        placeholder="Password"
        onChange={(e) => setPassword(e.target.value)}
        autoFocus
      />
      <button type="submit">Sign in</button>
      {error && <p className="error">{error}</p>}
    </form>
  );
}

// Placeholder screens — replaced in Tasks 14-16
function Today() {
  return <p>Today (coming soon)</p>;
}
function Scorecard() {
  return <p>Scorecard (coming soon)</p>;
}
function Settings() {
  return <p>Settings (coming soon)</p>;
}

export default function App() {
  const [authed, setAuthed] = useState<boolean | null>(null);
  const [probeError, setProbeError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("today");

  const probe = () => {
    setProbeError(null);
    apiGet("/targets")
      .then(() => setAuthed(true))
      .catch((e) => {
        if (e instanceof UnauthorizedError) setAuthed(false);
        else setProbeError("Can't reach the server.");
      });
  };

  useEffect(() => {
    probe();
  }, []);

  if (probeError) {
    return (
      <div className="center">
        <p className="error">{probeError}</p>
        <button onClick={probe}>Retry</button>
      </div>
    );
  }

  if (authed === null) return <p className="center">Loading…</p>;
  if (!authed) return <LoginScreen onLogin={() => setAuthed(true)} />;

  return (
    <div className="app">
      <main>
        {tab === "today" && <Today />}
        {tab === "scorecard" && <Scorecard />}
        {tab === "settings" && <Settings />}
      </main>
      <nav className="tabs">
        {(["today", "scorecard", "settings"] as Tab[]).map((t) => (
          <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>
            {t === "today" ? "Today" : t === "scorecard" ? "Scorecard" : "Settings"}
          </button>
        ))}
      </nav>
    </div>
  );
}
