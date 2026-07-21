import { useEffect, useState } from "react";
import { apiGet } from "../api";
import { targetLabel, weekLabel } from "../lib";

interface Metric { label: string; count: number; target: number; direction: string; hit: boolean }
interface Card { week_start: string; week_end: string; metrics: Record<string, Metric> }
interface History { weeks: Card[]; streaks: Record<string, number> }

const ORDER = ["gym", "social", "delivery", "alcohol"];

export default function Scorecard() {
  const [card, setCard] = useState<Card | null>(null);
  const [history, setHistory] = useState<History | null>(null);
  const [error, setError] = useState("");
  const [historyFailed, setHistoryFailed] = useState(false);

  useEffect(() => {
    apiGet<Card>("/scorecard").then(setCard).catch((e) => setError(e.message));
    apiGet<History>("/history?weeks=8").then(setHistory).catch(() => setHistoryFailed(true));
  }, []);

  if (error) return <p className="error">{error}</p>;
  if (!card) return <p className="center">Loading…</p>;

  return (
    <div>
      <h2>This week</h2>
      <p className="muted">{weekLabel(card.week_start)}</p>
      {ORDER.map((key) => {
        const m = card.metrics[key];
        return (
          <div className="card" key={key}>
            <strong className={m.hit ? "hit" : "miss"}>{m.hit ? "✅" : "❌"} {m.label}</strong>
            <p>
              {m.count} <span className="muted">(target {targetLabel(m.direction, m.target)})</span>
              {history && history.streaks[key] > 0 && (
                <span className="muted"> · {history.streaks[key]}-week streak</span>
              )}
            </p>
            {history && (
              <div style={{ display: "flex", gap: 4, marginTop: 8 }}>
                {history.weeks.map((w) => (
                  <div
                    key={w.week_start}
                    title={`${weekLabel(w.week_start)}: ${w.metrics[key].count}`}
                    style={{
                      flex: 1, height: 24, borderRadius: 4,
                      background: w.metrics[key].hit ? "#16a34a" : "#dc2626",
                      opacity: 0.35 + 0.65 * (history.weeks.indexOf(w) + 1) / history.weeks.length,
                    }}
                  />
                ))}
              </div>
            )}
          </div>
        );
      })}
      {historyFailed ? (
        <p className="muted">History unavailable.</p>
      ) : (
        <p className="muted">History: last 8 completed weeks, oldest → newest. Green = hit, red = miss.</p>
      )}
    </div>
  );
}
