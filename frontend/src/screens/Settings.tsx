import { useEffect, useState } from "react";
import { apiGet, apiSend } from "../api";
import { targetLabel } from "../lib";

interface Target { direction: string; value: number }
interface SettingsData {
  telegram_push: "on" | "off";
  telegram_configured: boolean;
  google_configured: boolean;
  gmail_last_run: string | null;
  gmail_last_status: string | null;
  calendar_last_run: string | null;
  calendar_last_status: string | null;
}

const LABELS: Record<string, string> = {
  gym: "Gym sessions / week",
  social: "Social events / week",
  delivery: "Delivery orders / week",
  alcohol: "Alcohol days / week",
};

export default function Settings() {
  const [targets, setTargets] = useState<Record<string, Target> | null>(null);
  const [settings, setSettings] = useState<SettingsData | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    apiGet<Record<string, Target>>("/targets").then(setTargets).catch((e) => setError(e.message));
    apiGet<SettingsData>("/settings").then(setSettings).catch((e) => setError(e.message));
  }, []);

  if (error) return <p className="error">{error}</p>;
  if (!targets || !settings) return <p className="center">Loading…</p>;

  const googleBroken =
    !settings.google_configured ||
    settings.gmail_last_status?.startsWith("error") ||
    settings.calendar_last_status?.startsWith("error");

  const updateTarget = async (metric: string, value: number) => {
    if (Number.isNaN(value) || value < 0) return;
    const updated = await apiSend<Record<string, Target>>("PUT", "/targets", { [metric]: value });
    setTargets(updated);
  };

  const togglePush = async () => {
    const next = settings.telegram_push === "on" ? "off" : "on";
    await apiSend("PUT", "/settings", { telegram_push: next });
    setSettings({ ...settings, telegram_push: next });
  };

  return (
    <div>
      <h2>Settings</h2>
      {googleBroken && (
        <div className="banner">
          ⚠️ Google disconnected — passive tracking is degraded. Re-run{" "}
          <code>python scripts/calendar_auth.py</code> and update the refresh token.
        </div>
      )}

      <div className="card">
        <h3>Weekly targets</h3>
        {Object.keys(LABELS).map((key) => (
          <label key={key} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "8px 0" }}>
            <span>
              {LABELS[key]} <span className="muted">({targetLabel(targets[key].direction, targets[key].value)})</span>
            </span>
            <input
              type="number" min={0} value={targets[key].value}
              style={{ width: 64, padding: 8, fontSize: 16 }}
              onChange={(e) => updateTarget(key, Number(e.target.value))}
            />
          </label>
        ))}
      </div>

      <div className="card">
        <h3>Telegram weekly push</h3>
        {settings.telegram_configured ? (
          <button className="big-btn" style={{ marginBottom: 0 }} onClick={togglePush}>
            {settings.telegram_push === "on" ? "On — Mondays (tap to turn off)" : "Off (tap to turn on)"}
          </button>
        ) : (
          <p className="muted">Telegram not configured (TELEGRAM_BOT_TOKEN unset).</p>
        )}
      </div>

      <div className="card">
        <h3>Sync status</h3>
        <p className="muted">Gmail: {settings.gmail_last_status ?? "never run"} ({settings.gmail_last_run ?? "—"})</p>
        <p className="muted">Calendar: {settings.calendar_last_status ?? "never run"} ({settings.calendar_last_run ?? "—"})</p>
      </div>
    </div>
  );
}
