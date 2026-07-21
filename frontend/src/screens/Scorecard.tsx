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
      <div className="screen-head">
        <h2>This week</h2>
        <p className="sub">{weekLabel(card.week_start)}</p>
      </div>

      <div className="ledger">
        {ORDER.map((key) => {
          const m = card.metrics[key];
          const ratio = m.target > 0 ? Math.min(m.count / m.target, 1) : m.count > 0 ? 1 : 0;
          const over = m.direction === "ceiling" && m.count > m.target;
          const streak = history?.streaks[key] ?? 0;
          return (
            <section className="metric" key={key}>
              <header>
                <span className={`mark ${m.hit ? "hit" : "miss"}`} aria-hidden="true" />
                <span className="m-name">{m.label}</span>
                <span className="m-count num">
                  {m.count}<span className="of"> of {targetLabel(m.direction, m.target)}</span>
                </span>
              </header>
              <div className={`meter${over ? " over" : ""}`}>
                <i style={{ width: `${(over ? 1 : ratio) * 100}%` }} />
              </div>
              <p className="m-sub">
                {m.hit
                  ? m.direction === "ceiling" ? "Within target" : "Target met"
                  : m.direction === "ceiling" ? "Over target" : "Not there yet"}
                {streak > 0 && ` · ${streak}-week streak`}
              </p>
              {history && (
                <div className="hist">
                  {history.weeks.map((w) => (
                    <i
                      key={w.week_start}
                      className={w.metrics[key].hit ? "hit" : "miss"}
                      title={`${weekLabel(w.week_start)}: ${w.metrics[key].count}`}
                    />
                  ))}
                </div>
              )}
            </section>
          );
        })}
      </div>

      <p className="footnote">
        {historyFailed
          ? "History unavailable."
          : "Small bars are the last 8 completed weeks, oldest first. Filled = hit, outlined = missed."}
      </p>
    </div>
  );
}
