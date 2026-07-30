import { useCallback, useEffect, useState } from "react";
import { apiGet, apiSend } from "../api";
import {
  addDays, buildSocialPatch, buildUncertainResolvePatch, mergeRemovedSocialEvents,
  subtotalsFromDay, targetLabel,
} from "../lib";
import DayNav from "../components/DayNav";
import SpendSubtotals from "../components/SpendSubtotals";

interface SocialEvent {
  gcal_event_id: string;
  title: string;
  start_at: string;
  end_at: string;
  source: string;
  amount: number | null;
  is_social: boolean;
  // True when the classifier hasn't decided confidently AND the user hasn't
  // answered — the row still shows (following the AI's lean for counting
  // purposes) but also carries the "social? Yes/No" ambiguity chip. See
  // docs/superpowers/specs/2026-07-30-social-classification-granularity-design.md.
  uncertain: boolean;
}

interface Ride {
  id: number;
  service: string;
  ride_at: string;
  // Resolved TRUE ride time (parsed trip time when known, else ride_at) —
  // display this, not ride_at, which is only the email-arrival time.
  ride_time: string;
  subject: string;
  amount: number | null;
  ai_is_work: boolean | null;
  user_is_work: boolean | null;
  is_work: boolean;
  is_cancellation: boolean | null;
}

interface TodayData {
  date: string;
  gym: boolean;
  alcohol_level: number | null;
  substances: boolean;
  deliveries: { service: string; subject: string; ordered_at: string; amount: number | null }[];
  social_events: SocialEvent[];
  rides: Ride[];
}

interface Metric { label: string; count: number; target: number; direction: string; hit: boolean }
interface Card { metrics: Record<string, Metric> }

const LEVEL_HINTS = ["a drink or two", "a solid night", "a heavy one"];
const STRIP_ORDER = ["gym", "social", "delivery", "alcohol", "substances"];
const STRIP_LABELS: Record<string, string> = {
  gym: "Gym", social: "Social", delivery: "Delivery", alcohol: "Alcohol", substances: "Subst.",
};

function timeLabel(iso: string): string {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "" : d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

interface Props {
  /** A date carried over from Week's "open this day" tap. Consumed once —
   * selects that day, then fires onConsumed() so the pin doesn't stick around
   * for the next time this screen mounts. */
  initialDate?: string | null;
  onConsumed?: () => void;
}

export default function Today({ initialDate, onConsumed }: Props = {}) {
  const [data, setData] = useState<TodayData | null>(null);
  const [week, setWeek] = useState<Card | null>(null);
  const [selected, setSelected] = useState<string | null>(null); // null = today
  const [todayIso, setTodayIso] = useState<string | null>(null);
  const [error, setError] = useState("");

  // Wait for todayIso so the "selected === null means today" equivalence still
  // holds when initialDate happens to be today — otherwise this would pin a
  // date that should behave like "no selection".
  useEffect(() => {
    if (initialDate == null || todayIso == null) return;
    setSelected(initialDate === todayIso ? null : initialDate);
    onConsumed?.();
  }, [initialDate, todayIso]);

  const [addingSocial, setAddingSocial] = useState(false);
  const [socialName, setSocialName] = useState("");
  const [socialAmount, setSocialAmount] = useState("");

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [editIsSocial, setEditIsSocial] = useState(true);
  const [editAmount, setEditAmount] = useState("");
  const [editLoaded, setEditLoaded] = useState<{ title: string; isSocial: boolean; amount: number | null }>({
    title: "", isSocial: true, amount: null,
  });

  // Detected social events the user just marked "Didn't happen" this
  // session, keyed by gcal_event_id. The day query filters resolved-social
  // events, so a refresh() no longer returns these — this map is the only
  // reason the row keeps rendering (with its own Undo). Deliberately not
  // persisted anywhere; it resets whenever the visible day changes below.
  const [removed, setRemoved] = useState<Record<string, SocialEvent>>({});

  useEffect(() => {
    setRemoved({});
  }, [data?.date]);

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

  const toggleSubstances = async () => {
    try {
      if (data.substances) await apiSend("DELETE", `/checkins/substances?date=${data.date}`);
      else await apiSend("POST", "/checkins", { type: "substances", date: data.date });
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const openAddSocial = () => {
    setEditingId(null);
    setSocialName("");
    setSocialAmount("");
    setAddingSocial(true);
  };

  const cancelAddSocial = () => setAddingSocial(false);

  const submitAddSocial = async () => {
    const name = socialName.trim();
    if (!name) return;
    try {
      const amount = socialAmount.trim() === "" ? undefined : Number(socialAmount);
      await apiSend("POST", "/social", { name, date: data.date, amount });
      setAddingSocial(false);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const openEditSocial = (e: SocialEvent) => {
    setAddingSocial(false);
    setEditingId(e.gcal_event_id);
    setEditTitle(e.title);
    setEditIsSocial(e.is_social);
    setEditAmount(e.amount !== null ? String(e.amount) : "");
    setEditLoaded({ title: e.title, isSocial: e.is_social, amount: e.amount });
  };

  const cancelEditSocial = () => setEditingId(null);

  const saveEditSocial = async () => {
    if (!editingId) return;
    const title = editTitle.trim();
    if (!title) return;
    try {
      // Only fields the user actually changed — an untouched checkbox or title
      // must never manufacture an override the user never made.
      const patch = buildSocialPatch({
        loadedTitle: editLoaded.title,
        loadedIsSocial: editLoaded.isSocial,
        loadedAmount: editLoaded.amount,
        title: editTitle,
        isSocial: editIsSocial,
        amountText: editAmount,
      });
      if (Object.keys(patch).length > 0) {
        await apiSend("PATCH", `/social/${editingId}`, patch);
      }
      setEditingId(null);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const deleteEditSocial = async () => {
    if (!editingId) return;
    try {
      await apiSend("DELETE", `/social/${editingId}`);
      setEditingId(null);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const didntHappenSocial = async (e: SocialEvent) => {
    try {
      // `removed` — "this occurrence didn't happen" — not `is_social`,
      // which means "this event type isn't social". Conflating the two
      // used to poison the classifier's few-shot examples with one-off
      // cancellations (see the granularity spec).
      await apiSend("PATCH", `/social/${e.gcal_event_id}`, { removed: true });
      setRemoved((prev) => ({ ...prev, [e.gcal_event_id]: e }));
      setEditingId(null);
      refresh();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const undoDidntHappen = async (e: SocialEvent) => {
    try {
      await apiSend("PATCH", `/social/${e.gcal_event_id}`, { removed: false });
      setRemoved((prev) => {
        const next = { ...prev };
        delete next[e.gcal_event_id];
        return next;
      });
      refresh();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const resolveUncertain = async (e: SocialEvent, isSocial: boolean) => {
    try {
      await apiSend("PATCH", `/social/${e.gcal_event_id}`, buildUncertainResolvePatch(isSocial));
      refresh();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const toggleRideWork = async (r: Ride) => {
    try {
      // Tapping also teaches future classification — the API folds confirmed
      // overrides back into the AI's examples on the next scan.
      await apiSend("PATCH", `/rides/${r.id}`, { is_work: !r.is_work });
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  // Merge in any just-removed rows the day query no longer returns, so the
  // "Nothing this day" empty state and the Undo row agree with each other.
  const socialRows = mergeRemovedSocialEvents(data.social_events, removed);
  const detections = data.deliveries.length + socialRows.length + data.rides.length;

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

        <button className={`item${data.substances ? " done" : ""}`} onClick={toggleSubstances}>
          <span className="dot">{data.substances ? "✓" : ""}</span>
          <span className="txt">
            <span className="t">Substances</span>
            <span className="s">{data.substances ? "Logged — tap to undo" : "Tap to log a day"}</span>
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

      <h2 className="section-label">Noticed quietly</h2>
      {detections === 0 && <p className="quiet empty">Nothing this day.</p>}
      {data.deliveries.map((d) => (
        <p className="quiet" key={d.ordered_at}>
          <span>{d.service} order</span>
          <span className="when">
            {d.amount != null && `$${d.amount.toFixed(2).replace(/\.00$/, "")} · `}
            {timeLabel(d.ordered_at)}
          </span>
        </p>
      ))}
      {data.rides.map((r) => {
        const unconfirmed = r.ai_is_work === true && r.user_is_work === null;
        return (
          <button className="quiet quiet-btn" key={r.id} onClick={() => toggleRideWork(r)}>
            <span>{r.service} {r.is_cancellation ? "cancellation fee" : "ride"}{unconfirmed ? " · work?" : ""}</span>
            <span className="when">
              {r.amount != null && `$${r.amount.toFixed(2).replace(/\.00$/, "")} · `}
              {timeLabel(r.ride_time)}
            </span>
          </button>
        );
      })}
      {socialRows.map((e) => {
        const isRemoved = Object.prototype.hasOwnProperty.call(removed, e.gcal_event_id);
        return (
          <div className="quiet-row" key={e.gcal_event_id}>
            {isRemoved ? (
              <div className="quiet quiet-removed">
                <span className="removed-title">{e.title}</span>
                <span className="when">
                  Removed · <button className="undo-btn" onClick={() => undoDidntHappen(e)}>Undo</button>
                </span>
              </div>
            ) : (
              <button className="quiet quiet-btn" onClick={() => openEditSocial(e)}>
                <span>{e.title}</span>
                <span className="when">
                  {e.amount !== null
                    ? `$${e.amount.toFixed(2).replace(/\.00$/, "")}`
                    : e.source === "manual"
                    ? "manual"
                    : e.is_social
                    ? "counted as social"
                    : "not counted"}
                </span>
              </button>
            )}
            {e.uncertain && !isRemoved && (
              <div className="uncertain-chip">
                <span>social?</span>
                <button onClick={() => resolveUncertain(e, true)}>Yes</button>
                <button onClick={() => resolveUncertain(e, false)}>No</button>
              </div>
            )}
            {editingId === e.gcal_event_id && !isRemoved && (
              <div className="social-form">
                <input
                  type="text"
                  value={editTitle}
                  onChange={(ev) => setEditTitle(ev.target.value)}
                  placeholder="Event name"
                  aria-label="Event name"
                />
                <label className="check">
                  <input
                    type="checkbox"
                    checked={editIsSocial}
                    onChange={(ev) => setEditIsSocial(ev.target.checked)}
                  />
                  Counts as social
                </label>
                <input
                  className="field-num"
                  type="number"
                  min="0"
                  step="0.01"
                  value={editAmount}
                  onChange={(ev) => setEditAmount(ev.target.value)}
                  placeholder="Cost"
                  aria-label="Cost"
                />
                <div className="row-actions">
                  <button onClick={cancelEditSocial}>Cancel</button>
                  {e.source === "manual" ? (
                    <button className="danger" onClick={deleteEditSocial}>Delete</button>
                  ) : (
                    <button className="danger" onClick={() => didntHappenSocial(e)}>Didn't happen</button>
                  )}
                  <button className="primary" onClick={saveEditSocial}>Save</button>
                </div>
              </div>
            )}
          </div>
        );
      })}

      {addingSocial ? (
        <div className="social-form">
          <input
            type="text"
            value={socialName}
            onChange={(ev) => setSocialName(ev.target.value)}
            placeholder="Event name"
            aria-label="Event name"
            autoFocus
          />
          <input
            className="field-num"
            type="number"
            min="0"
            step="0.01"
            value={socialAmount}
            onChange={(ev) => setSocialAmount(ev.target.value)}
            placeholder="Cost (optional)"
            aria-label="Cost"
          />
          <div className="row-actions">
            <button onClick={cancelAddSocial}>Cancel</button>
            <button className="primary" onClick={submitAddSocial}>Save</button>
          </div>
        </div>
      ) : (
        <button className="add-social-btn" onClick={openAddSocial}>+ Add social event</button>
      )}

      <SpendSubtotals rows={subtotalsFromDay(data)} title="Spent today" />

      {week && (
        <>
          <h2 className="section-label">This week</h2>
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
