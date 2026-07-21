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
  gym: "Gym sessions",
  social: "Social events",
  delivery: "Delivery orders",
  alcohol: "Alcohol days",
};

function statusLine(status: string | null, run: string | null): string {
  if (!status) return "Hasn't run yet";
  const when = run ? new Date(run) : null;
  const at = when && !isNaN(when.getTime())
    ? when.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })
    : "";
  return status === "ok" ? `OK${at ? ` · ${at}` : ""}` : status;
}

export default function Settings() {
  const [targets, setTargets] = useState<Record<string, Target> | null>(null);
  const [settings, setSettings] = useState<SettingsData | null>(null);
  const [error, setError] = useState("");
  const [saveError, setSaveError] = useState("");

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
    setSaveError("");
    if (Number.isNaN(value) || value < 0 || !Number.isInteger(value)) return;
    try {
      const updated = await apiSend<Record<string, Target>>("PUT", "/targets", { [metric]: value });
      setTargets(updated);
    } catch (e) {
      setSaveError((e as Error).message);
    }
  };

  const togglePush = async () => {
    setSaveError("");
    const next = settings.telegram_push === "on" ? "off" : "on";
    try {
      await apiSend("PUT", "/settings", { telegram_push: next });
      setSettings({ ...settings, telegram_push: next });
    } catch (e) {
      setSaveError((e as Error).message);
    }
  };

  return (
    <div>
      <div className="screen-head">
        <h2>Settings</h2>
      </div>

      {saveError && <p className="error">{saveError}</p>}
      {googleBroken && (
        <div className="banner">
          Google is disconnected, so passive tracking is degraded. Re-run{" "}
          <code>python scripts/calendar_auth.py</code> and update the refresh token.
        </div>
      )}

      <p className="section-label">Weekly targets</p>
      <div className="group">
        {Object.keys(LABELS).map((key) => (
          <label className="row" key={key}>
            <span className="grow">
              {LABELS[key]}
              <span className="hint">
                {targets[key].direction === "ceiling" ? "At most" : "At least"}{" "}
                {targetLabel(targets[key].direction, targets[key].value).slice(1)} per week
              </span>
            </span>
            <input
              className="field-num"
              type="number" min={0} step={1} value={targets[key].value}
              onChange={(e) => updateTarget(key, Number(e.target.value))}
            />
          </label>
        ))}
      </div>

      <p className="section-label">Weekly summary</p>
      <div className="group">
        {settings.telegram_configured ? (
          <label className="row">
            <span className="grow">
              Telegram push
              <span className="hint">One scorecard message, Monday mornings</span>
            </span>
            <span className="switch">
              <input
                type="checkbox"
                checked={settings.telegram_push === "on"}
                onChange={togglePush}
                aria-label="Telegram weekly push"
              />
              <span className="knob" />
            </span>
          </label>
        ) : (
          <div className="row">
            <span className="grow">
              Telegram push
              <span className="hint">Not configured — TELEGRAM_BOT_TOKEN is unset</span>
            </span>
          </div>
        )}
      </div>

      <p className="section-label">Sync</p>
      <div className="group">
        <div className="row">
          <span className="grow">
            Gmail receipts
            <span className="hint">{statusLine(settings.gmail_last_status, settings.gmail_last_run)}</span>
          </span>
        </div>
        <div className="row">
          <span className="grow">
            Calendar events
            <span className="hint">{statusLine(settings.calendar_last_status, settings.calendar_last_run)}</span>
          </span>
        </div>
      </div>
    </div>
  );
}
