import { useEffect, useState } from "react";
import { apiGet } from "../api";
import { addDays, mondayOf, targetLabel } from "../lib";
import WeekNav from "../components/WeekNav";
import TrendChart from "../components/TrendChart";
import WeekdayHeatmap from "../components/WeekdayHeatmap";

interface Metric { label: string; count: number; target: number; direction: string; hit: boolean }
interface Card {
  week_start: string; week_end: string; metrics: Record<string, Metric>;
  delivery_spend: number; social_spend: number;
}
interface Insights {
  weeks: Card[];
  streaks: Record<string, number>;
  weekday_counts: Record<string, number[]>;
  noticings: string[];
}
interface Reflection { week_start: string; text: string }

const ORDER = ["gym", "social", "delivery", "alcohol", "substances"];

function hasAnyData(ins: Insights): boolean {
  return ins.weeks.some((w) => Object.values(w.metrics).some((m) => m.count > 0));
}

export default function Scorecard() {
  const [card, setCard] = useState<Card | null>(null);
  const [currentWeekStart, setCurrentWeekStart] = useState<string | null>(null);
  const [currentWeekEnd, setCurrentWeekEnd] = useState<string | null>(null);
  const [weekStart, setWeekStart] = useState<string | null>(null); // null = current
  const [insights, setInsights] = useState<Insights | null>(null);
  const [reflection, setReflection] = useState<Reflection | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    apiGet<Card>(`/scorecard${weekStart ? `?week_start=${weekStart}` : ""}`)
      .then((c) => {
        setCard(c);
        if (!weekStart) {
          setCurrentWeekStart(c.week_start);
          setCurrentWeekEnd(c.week_end);
        }
      })
      .catch((e) => setError(e.message));
  }, [weekStart]);

  useEffect(() => {
    apiGet<Insights>("/insights?weeks=12").then(setInsights).catch(() => setInsights(null));
  }, []);

  useEffect(() => {
    apiGet<Reflection | null>("/reflection").then(setReflection).catch(() => setReflection(null));
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
        max={currentWeekEnd ?? card.week_end}
        onPick={(iso) => {
          const monday = mondayOf(iso);
          setWeekStart(monday === currentWeekStart ? null : monday);
        }}
      />

      <div className="ledger">
        {ORDER.map((key) => {
          const m = card.metrics[key];
          const ratio = m.target > 0 ? Math.min(m.count / m.target, 1) : m.count > 0 ? 1 : 0;
          const over = m.direction === "ceiling" && m.count > m.target;
          const streak = insights?.streaks[key] ?? 0;
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
                {key === "delivery" && card.delivery_spend > 0 &&
                  ` · $${card.delivery_spend.toFixed(2).replace(/\.00$/, "")} spent`}
                {key === "social" && card.social_spend > 0 &&
                  ` · $${card.social_spend.toFixed(2).replace(/\.00$/, "")} spent`}
              </p>
            </section>
          );
        })}
      </div>

      {insights && hasAnyData(insights) && (
        <>
          <p className="section-label">Trends · last 12 weeks</p>
          <div className="trends">
            {ORDER.map((key) => (
              <div className="trend-row" key={key}>
                <span className="trend-name">{card.metrics[key].label}</span>
                <TrendChart
                  points={insights.weeks.map((w) => ({
                    count: w.metrics[key].count, hit: w.metrics[key].hit, weekStart: w.week_start,
                  }))}
                  target={card.metrics[key].target}
                  direction={card.metrics[key].direction}
                />
              </div>
            ))}
          </div>

          <p className="section-label">Patterns · by weekday, last 8 weeks</p>
          <WeekdayHeatmap
            rows={ORDER.map((key) => ({
              label: card.metrics[key].label,
              counts: insights.weekday_counts[key] ?? [0, 0, 0, 0, 0, 0, 0],
              caution: card.metrics[key].direction === "ceiling",
            }))}
          />

          {insights.noticings.length > 0 && (
            <>
              <p className="section-label">Noticings</p>
              {insights.noticings.map((n) => (
                <p className="quiet" key={n}><span>{n}</span></p>
              ))}
            </>
          )}
        </>
      )}
      {insights && !hasAnyData(insights) && (
        <p className="footnote">Not enough history yet — insights appear after a few weeks.</p>
      )}

      {reflection && (
        <>
          <p className="section-label">Last week</p>
          <p className="reflection">{reflection.text}</p>
        </>
      )}
    </div>
  );
}
