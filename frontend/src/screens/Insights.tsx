import { useEffect, useState } from "react";
import { apiGet, apiSend } from "../api";
import { targetLabel, weekRangeLabel } from "../lib";
import TrendChart from "../components/TrendChart";
import WeekdayHeatmap from "../components/WeekdayHeatmap";

interface Metric { label: string; count: number; target: number; direction: string; hit: boolean }
interface Card { week_start: string; metrics: Record<string, Metric> }
interface InsightsData {
  weeks: Card[];
  streaks: Record<string, number>;
  weekday_counts: Record<string, number[]>;
  noticings: string[];
}
interface Reflection { week_start: string; text: string }

const ORDER = ["gym", "social", "delivery", "alcohol", "substances"];

function hasAnyData(ins: InsightsData): boolean {
  return ins.weeks.some((w) => Object.values(w.metrics).some((m) => m.count > 0));
}

export default function Insights() {
  // ── Behavior view data — moved from Scorecard.tsx unchanged ──
  const [card, setCard] = useState<Card | null>(null);
  const [insights, setInsights] = useState<InsightsData | null>(null);
  const [reflection, setReflection] = useState<Reflection | null>(null);
  const [selected, setSelected] = useState<Record<string, number | null>>({});

  useEffect(() => {
    apiGet<Card>("/scorecard").then(setCard).catch(() => setCard(null));
  }, []);

  useEffect(() => {
    apiGet<InsightsData>("/insights?weeks=12").then(setInsights).catch(() => setInsights(null));
  }, []);

  useEffect(() => {
    apiSend<Reflection | null>("POST", "/reflection").then(setReflection).catch(() => setReflection(null));
  }, []);

  return (
    <div>
      {card && insights && hasAnyData(insights) && (
        <>
          <h2 className="section-label">Trends · last 12 weeks</h2>
          <div className="trends">
            {ORDER.map((key) => {
              const m = card.metrics[key];
              const points = insights.weeks.map((w) => ({
                count: w.metrics[key].count, hit: w.metrics[key].hit, weekStart: w.week_start,
              }));
              const allZero = points.every((p) => p.count === 0);
              const selectedIndex = selected[key] ?? null;
              const selectedPoint = selectedIndex !== null ? points[selectedIndex] : null;
              return (
                <div className="trend-row" key={key}>
                  <div className="trend-row-head">
                    <span className="trend-name">{m.label}</span>
                    <span className="trend-current">
                      <span className="num">{m.count}</span> of {targetLabel(m.direction, m.target)}
                    </span>
                  </div>
                  {allZero ? (
                    <p className="trend-empty">No data yet</p>
                  ) : (
                    <>
                      <TrendChart
                        points={points}
                        target={m.target}
                        direction={m.direction}
                        onSelect={(i) =>
                          setSelected((prev) => ({ ...prev, [key]: prev[key] === i ? null : i }))
                        }
                      />
                      {selectedPoint && (
                        <p className="trend-caption">
                          {weekRangeLabel(selectedPoint.weekStart)} · {selectedPoint.count}
                        </p>
                      )}
                    </>
                  )}
                </div>
              );
            })}
          </div>

          <h2 className="section-label">Patterns · by weekday, last 8 weeks</h2>
          <WeekdayHeatmap
            rows={ORDER.map((key) => ({
              label: card.metrics[key].label,
              counts: insights.weekday_counts[key] ?? [0, 0, 0, 0, 0, 0, 0],
              caution: card.metrics[key].direction === "ceiling",
            }))}
          />

          <details className="numbers">
            <summary>Show the numbers</summary>
            <div className="numbers-scroll">
              <table className="numbers-table">
                <thead>
                  <tr>
                    <th>Week</th>
                    {ORDER.map((key) => <th key={key}>{card.metrics[key].label}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {[...insights.weeks].reverse().map((w) => (
                    <tr key={w.week_start}>
                      <td>{weekRangeLabel(w.week_start)}</td>
                      {ORDER.map((key) => (
                        <td key={key} className="num">{w.metrics[key].count}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>

          {insights.noticings.length > 0 && (
            <>
              <h2 className="section-label">Noticings</h2>
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
          <h2 className="section-label">Last week</h2>
          <p className="reflection">{reflection.text}</p>
        </>
      )}
    </div>
  );
}
