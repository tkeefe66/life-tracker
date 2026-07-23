import { useEffect, useState } from "react";
import { apiGet, login, UnauthorizedError } from "./api";
import Today from "./screens/Today";
import Scorecard from "./screens/Scorecard";
import Insights from "./screens/Insights";
import Settings from "./screens/Settings";

type Tab = "today" | "scorecard" | "insights" | "settings";

const TAB_META: { id: Tab; label: string; icon: JSX.Element }[] = [
  {
    id: "today",
    label: "Today",
    icon: (
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
        <circle cx="10" cy="10" r="7.25" stroke="currentColor" strokeWidth="1.5" />
        <circle cx="10" cy="10" r="2.5" fill="currentColor" />
      </svg>
    ),
  },
  {
    id: "scorecard",
    label: "Week",
    icon: (
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
        <path d="M4 16V9M10 16V4M16 16v-5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      </svg>
    ),
  },
  {
    id: "insights",
    label: "Insights",
    icon: (
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
        <path d="M3 14l4.5-5 3 3L17 5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx="17" cy="5" r="1.4" fill="currentColor" />
      </svg>
    ),
  },
  {
    id: "settings",
    label: "Settings",
    icon: (
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
        <path d="M3 6h9M15.5 6H17M3 14h2.5M9 14h8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        <circle cx="13.5" cy="6" r="1.75" stroke="currentColor" strokeWidth="1.5" />
        <circle cx="6.5" cy="14" r="1.75" stroke="currentColor" strokeWidth="1.5" />
      </svg>
    ),
  },
];

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
      <h1 className="wordmark">On Track</h1>
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

export default function App() {
  const [authed, setAuthed] = useState<boolean | null>(null);
  const [probeError, setProbeError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("today");
  // A date tapped on Week's day-by-day view, carried to Today and consumed
  // once it lands there — see Today's initialDate/onConsumed.
  const [pendingDay, setPendingDay] = useState<string | null>(null);

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
        {tab === "today" && (
          <Today initialDate={pendingDay} onConsumed={() => setPendingDay(null)} />
        )}
        {tab === "scorecard" && (
          <Scorecard onOpenDay={(iso) => { setPendingDay(iso); setTab("today"); }} />
        )}
        {tab === "insights" && <Insights />}
        {tab === "settings" && <Settings />}
      </main>
      <nav className="tabs">
        {TAB_META.map((t) => (
          <button
            key={t.id}
            className={tab === t.id ? "active" : ""}
            aria-current={tab === t.id ? "page" : undefined}
            onClick={() => setTab(t.id)}
          >
            {t.icon}
            {t.label}
          </button>
        ))}
      </nav>
    </div>
  );
}
