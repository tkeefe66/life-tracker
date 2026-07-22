import { useCallback, useEffect, useState } from "react";
import { apiGet, apiSend } from "../api";
import { addDays, targetLabel } from "../lib";
import DayNav from "../components/DayNav";

interface TodayData {
  date: string;
  gym: boolean;
  alcohol_level: number | null;
  deliveries: { service: string; subject: string; ordered_at: string }[];
  social_events: { title: string; start_at: string; end_at: string }[];
}

interface Metric { label: string; count: number; target: number; direction: string; hit: boolean }
interface Card { metrics: Record<string, Metric> }

const LEVEL_HINTS = ["a drink or two", "a solid night", "a heavy one"];
const STRIP_ORDER = ["gym", "social", "delivery", "alcohol"];
const STRIP_LABELS: Record<string, string> = {
  gym: "Gym", social: "Social", delivery: "Delivery", alcohol: "Alcohol",
};

function timeLabel(iso: string): string {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "" : d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

export default function Today() {
  const [data, setData] = useState<TodayData | null>(null);
  const [week, setWeek] = useState<Card | null>(null);
  const [selected, setSelected] = useState<string | null>(null); // null = today
  const [todayIso, setTodayIso] = useState<string | null>(null);
  const [error, setError] = useState("");

  const refresh = useCallback(() => {
    apiGet<TodayData>(`/today${selected ? `?date=${selected}` : ""}`)
      .then((d) => {
        setData(d);
        if (!selected) setTodayIso(d.date);
      })
      .catch((e) => setError(e.message));
    apiGet<Card>(`/scorecard${selected ? `?week_start=${selected}` : ""}`)
      .then(setWeek)
      .catch(() => setWeek(null));
  }, [selected]);
  useEffect(refresh, [refresh]);

  if (error) return <p className="error">{error}</p>;
  if (!data) return <p className="center">Loading…</p>;

  const toggleGym = async () => {
    try {
      if (data.gym) await apiSend("DELETE", `/checkins/gym?date=${data.date}`);
      else await apiSend("POST", "/checkins", { type: "gym", date: data.date });
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const logAlcohol = async (level: number) => {
    try {
      await apiSend("POST", "/checkins", { type: "alcohol", level, date: data.date });
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const undoAlcohol = async () => {
    try {
      await apiSend("DELETE", `/checkins/alcohol?date=${data.date}`);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const detections = data.deliveries.length + data.social_events.length;

  return (
    <div>
      <DayNav
        date={data.date}
        todayIso={todayIso ?? data.date}
        onPrev={() => setSelected(addDays(data.date, -1))}
        onNext={() => {
          const next = addDays(data.date, 1);
          setSelected(next === todayIso ? null : next);
        }}
        onPick={(iso) => setSelected(iso === todayIso ? null : iso)}
      />

      <div className="stack">
        <button className={`item${data.gym ? " done" : ""}`} onClick={toggleGym}>
          <span className="dot">{data.gym ? "✓" : ""}</span>
          <span className="txt">
            <span className="t">Gym</span>
            <span className="s">{data.gym ? "Logged — tap to undo" : "Tap to log a session"}</span>
          </span>
        </button>

        {data.alcohol_level === null ? (
          <div className="item">
            <span className="dot"></span>
            <span className="txt">
              <span className="t">Alcohol</span>
              <span className="s">Tap a level if you drank · 1 light — 3 heavy</span>
            </span>
            <span className="chips">
              {[1, 2, 3].map((lvl) => (
                <button key={lvl} aria-label={`Log alcohol, level ${lvl} — ${LEVEL_HINTS[lvl - 1]}`} onClick={() => logAlcohol(lvl)}>
                  {lvl}
                </button>
              ))}
            </span>
          </div>
        ) : (
          <button className="item done" onClick={undoAlcohol}>
            <span className="dot num">{data.alcohol_level}</span>
            <span className="txt">
              <span className="t">Alcohol</span>
              <span className="s">Logged {LEVEL_HINTS[data.alcohol_level - 1]} — tap to undo</span>
            </span>
          </button>
        )}
      </div>

      <p className="section-label">Noticed quietly</p>
      {detections === 0 && <p className="quiet empty">Nothing this day.</p>}
      {data.deliveries.map((d) => (
        <p className="quiet" key={d.ordered_at}>
          <span>{d.service} order</span>
          <span className="when">{timeLabel(d.ordered_at)}</span>
        </p>
      ))}
      {data.social_events.map((e) => (
        <p className="quiet" key={e.start_at}>
          <span>{e.title}</span>
          <span className="when">counted as social</span>
        </p>
      ))}

      {week && (
        <>
          <p className="section-label">This week</p>
          <div className="week-strip">
            {STRIP_ORDER.map((key) => {
              const m = week.metrics[key];
              if (!m) return null;
              const ratio = m.target > 0 ? Math.min(m.count / m.target, 1) : m.count > 0 ? 1 : 0;
              const over = m.direction === "ceiling" && m.count > m.target;
              return (
                <div key={key}>
                  <span className="wl">
                    <span>{STRIP_LABELS[key]}</span>
                    <span className="num">{m.count}/{targetLabel(m.direction, m.target).slice(1)}</span>
                  </span>
                  <span className={`meter${over ? " over" : ""}`}>
                    <i style={{ width: `${(over ? 1 : ratio) * 100}%` }} />
                  </span>
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
