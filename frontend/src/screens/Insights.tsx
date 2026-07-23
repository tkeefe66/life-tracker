import { useEffect, useState } from "react";
import { apiGet } from "../api";
import { dayLabel, money, serviceLabel, targetLabel, weekRangeLabel, type SpendRow } from "../lib";
import TrendChart from "../components/TrendChart";
import WeekdayHeatmap from "../components/WeekdayHeatmap";
import SpendChart, { type SpendWeekPoint } from "../components/SpendChart";
import SpendSubtotals from "../components/SpendSubtotals";

interface Metric { label: string; count: number; target: number; direction: string; hit: boolean }
interface Card { week_start: string; metrics: Record<string, Metric> }
interface InsightsData {
  weeks: Card[];
  streaks: Record<string, number>;
  weekday_counts: Record<string, number[]>;
  noticings: string[];
}
interface Reflection { week_start: string; text: string }

interface SpendItem { kind: string; service: string; label: string; at: string; amount: number }
interface SpendData {
  weeks: SpendWeekPoint[];
  by_service: SpendRow[];
  items: SpendItem[];
}

const ORDER = ["gym", "social", "delivery", "alcohol", "substances"];

const LEGEND: { key: "delivery" | "rides" | "social"; label: string; token: string }[] = [
  { key: "delivery", label: "Delivery", token: "var(--chart-delivery)" },
  { key: "rides", label: "Rides", token: "var(--chart-rides)" },
  { key: "social", label: "Social", token: "var(--chart-social)" },
];

function hasAnyData(ins: InsightsData): boolean {
  return ins.weeks.some((w) => Object.values(w.metrics).some((m) => m.count > 0));
}

/** Preserves the items' existing newest-first order, just bucketed by day. */
function groupItemsByDay(items: SpendItem[]): [string, SpendItem[]][] {
  const map = new Map<string, SpendItem[]>();
  for (const item of items) {
    const day = item.at.slice(0, 10);
    const bucket = map.get(day);
    if (bucket) bucket.push(item);
    else map.set(day, [item]);
  }
  return Array.from(map.entries());
}

export default function Insights() {
  const [view, setView] = useState<"behavior" | "money">("behavior");

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
    apiGet<Reflection | null>("/reflection").then(setReflection).catch(() => setReflection(null));
  }, []);

  // ── Money view data ──
  const [spend, setSpend] = useState<SpendData | null>(null);
  const [selectedWeek, setSelectedWeek] = useState<number | null>(null);

  useEffect(() => {
    apiGet<SpendData>("/spend?weeks=12").then(setSpend).catch(() => setSpend(null));
  }, []);

  const heroTotal = spend ? spend.weeks.reduce((sum, w) => sum + w.delivery + w.rides + w.social, 0) : 0;
  const selectedWeekPoint = spend && selectedWeek !== null ? spend.weeks[selectedWeek] : null;

  return (
    <div>
      <div className="segment">
        <button className={view === "behavior" ? "active" : ""} onClick={() => setView("behavior")}>
          Behavior
        </button>
        <button className={view === "money" ? "active" : ""} onClick={() => setView("money")}>
          Money
        </button>
      </div>

      {view === "behavior" && (
        <>
          {card && insights && hasAnyData(insights) && (
            <>
              <p className="section-label">Trends · last 12 weeks</p>
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

              <p className="section-label">Patterns · by weekday, last 8 weeks</p>
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
        </>
      )}

      {view === "money" && spend && (
        <>
          <p className="money-hero">{money(heroTotal)}</p>
          <p className="money-hero-sub">last 12 weeks</p>

          <SpendChart
            weeks={spend.weeks}
            onSelect={(i) => setSelectedWeek((prev) => (prev === i ? null : i))}
          />

          <div className="legend">
            {LEGEND.map((l) => (
              <span className="legend-item" key={l.key}>
                <span className="legend-swatch" style={{ background: l.token }} />
                {l.label}
              </span>
            ))}
          </div>

          {selectedWeekPoint && (
            <p className="trend-caption">
              {weekRangeLabel(selectedWeekPoint.week_start)} · Delivery {money(selectedWeekPoint.delivery)} ·
              {" "}Rides {money(selectedWeekPoint.rides)} · Social {money(selectedWeekPoint.social)}
            </p>
          )}

          <SpendSubtotals rows={spend.by_service} title="By service · last 12 weeks" />

          {spend.items.length > 0 && (
            <>
              <p className="section-label">Itemized</p>
              {groupItemsByDay(spend.items).map(([day, dayItems]) => (
                <div key={day}>
                  <p className="item-day">{dayLabel(day)}</p>
                  {dayItems.map((item, idx) => (
                    <p className="quiet" key={`${item.kind}:${item.service}:${item.at}:${idx}`}>
                      <span>{serviceLabel(item.kind, item.service)} — {item.label}</span>
                      <span className="when">{money(item.amount)}</span>
                    </p>
                  ))}
                </div>
              ))}
            </>
          )}
        </>
      )}
    </div>
  );
}
