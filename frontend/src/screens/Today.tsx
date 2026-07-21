import { useCallback, useEffect, useState } from "react";
import { apiGet, apiSend } from "../api";

interface TodayData {
  date: string;
  gym: boolean;
  alcohol_level: number | null;
  deliveries: { service: string; subject: string; ordered_at: string }[];
  social_events: { title: string; start_at: string; end_at: string }[];
}

const LEVELS = ["1 — a drink or two", "2 — solid night", "3 — blackout"];

export default function Today() {
  const [data, setData] = useState<TodayData | null>(null);
  const [level, setLevel] = useState(1);
  const [error, setError] = useState("");

  const refresh = useCallback(() => {
    apiGet<TodayData>("/today").then(setData).catch((e) => setError(e.message));
  }, []);
  useEffect(refresh, [refresh]);

  if (error) return <p className="error">{error}</p>;
  if (!data) return <p className="center">Loading…</p>;

  const toggleGym = async () => {
    try {
      if (data.gym) await apiSend("DELETE", "/checkins/gym");
      else await apiSend("POST", "/checkins", { type: "gym" });
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const toggleAlcohol = async () => {
    try {
      if (data.alcohol_level !== null) await apiSend("DELETE", "/checkins/alcohol");
      else await apiSend("POST", "/checkins", { type: "alcohol", level });
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <div>
      <h2>{data.date}</h2>
      <button className="big-btn" style={{ background: data.gym ? "#16a34a" : "#e5e7eb", color: data.gym ? "#fff" : "#111" }} onClick={toggleGym}>
        {data.gym ? "Gym ✓ (tap to undo)" : "I went to the gym"}
      </button>

      <div className="card">
        {data.alcohol_level === null && (
          <select value={level} onChange={(e) => setLevel(Number(e.target.value))} style={{ width: "100%", padding: 10, marginBottom: 8 }}>
            {LEVELS.map((label, i) => (
              <option key={i} value={i + 1}>{label}</option>
            ))}
          </select>
        )}
        <button className="big-btn" style={{ marginBottom: 0, background: data.alcohol_level !== null ? "#b45309" : "#e5e7eb", color: data.alcohol_level !== null ? "#fff" : "#111" }} onClick={toggleAlcohol}>
          {data.alcohol_level !== null ? `Drank — level ${data.alcohol_level} (tap to undo)` : "I drank today"}
        </button>
      </div>

      <div className="card">
        <h3>Detected today</h3>
        {data.deliveries.length === 0 && data.social_events.length === 0 && (
          <p className="muted">Nothing yet — that's a good sign.</p>
        )}
        {data.deliveries.map((d) => (
          <p key={d.ordered_at}>🥡 {d.service}: {d.subject}</p>
        ))}
        {data.social_events.map((e) => (
          <p key={e.start_at}>🎉 {e.title}</p>
        ))}
      </div>
    </div>
  );
}
