import { useEffect, useState } from "react";
import { apiGet } from "../api";
import { addDays, targetLabel, weekLabel } from "../lib";
import WeekNav from "../components/WeekNav";

interface Metric { label: string; count: number; target: number; direction: string; hit: boolean }
interface Card { week_start: string; week_end: string; metrics: Record<string, Metric> }
interface History { weeks: Card[]; streaks: Record<string, number> }

const ORDER = ["gym", "social", "delivery", "alcohol"];

export default function Scorecard() {
  const [card, setCard] = useState<Card | null>(null);
  const [currentWeekStart, setCurrentWeekStart] = useState<string | null>(null);
  const [weekStart, setWeekStart] = useState<string | null>(null); // null = current
  const [history, setHistory] = useState<History | null>(null);
  const [error, setError] = useState("");
  const [historyFailed, setHistoryFailed] = useState(false);

  useEffect(() => {
    apiGet<Card>(`/scorecard${weekStart ? `?week_start=${weekStart}` : ""}`)
      .then((c) => {
        setCard(c);
        if (!weekStart) setCurrentWeekStart(c.week_start);
      })
      .catch((e) => setError(e.message));
  }, [weekStart]);

  useEffect(() => {
    apiGet<History>("/history?weeks=8").then(setHistory).catch(() => setHistoryFailed(true));
  }, []);

  if (error) return <p className="error">{error}</p>;
  if (!card) return <p className="center">Loading…</p>;

  return (
    <div>
      <WeekNav
        weekStart={card.week_start}
        isCurrent={card.week_start === (currentWeekStart ?? card.week_start)}
        onPrev={() => setWeekStart(addDays(card.week_start, -7))}
        onNext={() => {
          const next = addDays(card.week_start, 7);
          setWeekStart(next === currentWeekStart ? null : next);
        }}
      />

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
                {streak > 0 && weekStart === null && ` · ${streak}-week streak`}
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
