const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function parseDay(iso: string): Date {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}

export function weekLabel(weekStartIso: string): string {
  const start = parseDay(weekStartIso);
  const end = new Date(start);
  end.setDate(end.getDate() + 6);
  return `${MONTHS[start.getMonth()]} ${start.getDate()} – ${MONTHS[end.getMonth()]} ${end.getDate()}`;
}

export function targetLabel(direction: string, target: number): string {
  return `${direction === "ceiling" ? "≤" : "≥"}${target}`;
}

/**
 * The y-axis top for a trend chart: the tallest bar and the target line both
 * need to sit comfortably inside the plot, never flush with the top edge (a
 * flush target line reads as "off the chart" rather than "the ceiling").
 */
export function niceMax(values: number[], target: number): number {
  const rawMax = Math.max(...values, target, 1);
  let max = Math.ceil(rawMax);
  if (max === target) max += 1;
  return max;
}

/**
 * A compact week-range label for chart axes: same-month weeks show the month
 * once ("Jul 13–19"), weeks crossing a month boundary repeat it ("Jun 29–Jul 5").
 */
export function weekRangeLabel(weekStartIso: string): string {
  const start = parseDay(weekStartIso);
  const end = new Date(start);
  end.setDate(end.getDate() + 6);
  const startMonth = MONTHS[start.getMonth()];
  const endMonth = MONTHS[end.getMonth()];
  return startMonth === endMonth
    ? `${startMonth} ${start.getDate()}–${end.getDate()}`
    : `${startMonth} ${start.getDate()}–${endMonth} ${end.getDate()}`;
}

const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const MONTHS_FULL = ["January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December"];

export function dayLabel(iso: string): string {
  const d = parseDay(iso);
  return `${DAYS[d.getDay()]}, ${MONTHS_FULL[d.getMonth()]} ${d.getDate()}`;
}

export function addDays(iso: string, delta: number): string {
  const d = parseDay(iso);
  d.setDate(d.getDate() + delta);
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${mm}-${dd}`;
}

export function mondayOf(iso: string): string {
  const d = parseDay(iso);
  return addDays(iso, -((d.getDay() + 6) % 7));
}

export function relativeDayLabel(iso: string, todayIso: string): string {
  if (iso === todayIso) return "Today";
  if (iso === addDays(todayIso, -1)) return "Yesterday";
  const d = parseDay(iso);
  const base = `${DAYS[d.getDay()].slice(0, 3)}, ${MONTHS[d.getMonth()]} ${d.getDate()}`;
  return d.getFullYear() === parseDay(todayIso).getFullYear()
    ? base
    : `${base}, ${d.getFullYear()}`;
}

export interface SocialEditState {
  loadedTitle: string;
  loadedIsSocial: boolean;
  loadedAmount: number | null;
  title: string;
  isSocial: boolean;
  amountText: string;
}

export interface SocialPatch {
  title?: string;
  is_social?: boolean;
  amount?: number | null;
}

/** Left-hand label for a spend-subtotal row: rides get a "rides" suffix,
 * delivery services print as-is, and any social row collapses to "Social". */
export function serviceLabel(kind: string, service: string): string {
  if (kind === "ride") return `${service} rides`;
  if (kind === "social") return "Social";
  return service;
}

/** `$16.31`, whole dollars trimmed (`$20`) — a real `$0` still shows, it is
 * never treated as "nothing to show" (that's the caller's job via null checks). */
export function money(amount: number): string {
  return `$${amount.toFixed(2).replace(/\.00$/, "")}`;
}

/**
 * Button -> `user_flow` mapping for the triage worklist (spec §6.1). The user
 * never sees the word "flow" or any of its six values — these are the plain-
 * language labels the buttons show, and this table is the whole correctness
 * surface: get a row wrong and a correction writes the wrong flow silently.
 * Two queues, two answer sets — "Moved it" (ambiguous/outflow queue) and
 * "Moved from another account" (inflow_unknown queue) are different buttons
 * that both resolve to `transfer`.
 */
export interface TriageChoice { label: string; flow: string }
export const TRIAGE_CHOICES: { outflow: TriageChoice[]; inflow: TriageChoice[] } = {
  outflow: [
    { label: "Spent it", flow: "spending" },
    { label: "Moved it", flow: "transfer" },
    { label: "Paid a card", flow: "card_payment" },
    { label: "Saved / invested", flow: "investment" },
  ],
  inflow: [
    { label: "It's income", flow: "income" },
    { label: "Moved from another account", flow: "transfer" },
    { label: "Refunded", flow: "refund" },
  ],
};

const FLOW_LABELS: Record<string, string> = {
  card_payment: "Paid off cards",
  transfer: "Moved between accounts",
  investment: "Into investments",
  income: "Money in",
  refund: "Refunded",
};

/** Display label for a resolved bank flow (§5.2 row headings). An unresolved
 * or unrecognized value is returned as-is rather than throwing — a display
 * helper should degrade, not crash the money screen. */
export function flowLabel(flow: string): string {
  return FLOW_LABELS[flow] ?? flow;
}

/** Triage-row suggestion hint (spec §5): `"looks like: {label}"` for a flow
 * the AI suggested, using `flowLabel`'s plain language verbatim rather than a
 * separate wording. Unlike `flowLabel`, an unrecognized flow — or no
 * suggestion at all (`null`/`undefined`) — degrades to `""` (no hint), never
 * a raw token; `FLOW_LABELS` is deliberately missing `spending` (there is no
 * "looks like: spending" — an unresolved flow is the row's whole reason for
 * being in the queue), so that case falls through the same unknown-flow path. */
export function suggestionHint(flow: string | null | undefined): string {
  if (flow == null || !(flow in FLOW_LABELS)) return "";
  return `looks like: ${flowLabel(flow)}`;
}

/** The permanent coverage footnote (spec §5.6 / §4): explains why data
 * before `covered_from` doesn't exist, rather than looking like a gap. No
 * coverage yet (`null`, e.g. before the first sync) renders nothing. */
export function coverageNote(coveredFrom: string | null): string {
  if (coveredFrom == null) return "";
  const { monthDay } = dayRowDate(coveredFrom);
  return `Bank data starts ${monthDay}. SimpleFIN keeps 90 days, so nothing before that exists.`;
}

export interface WeekSpendPoint { week_start: string; spending: number; partial: boolean }

/** Tap caption for a bar in the weekly bank-spending chart (spec §5.1): the
 * week range, the total, and "partial week" only when the week is partial —
 * a zero-spend week still shows `$0`, never an empty gap. A week that nets
 * negative (refunds outweighing spending) shows the true net with a U+2212
 * minus before the `$`, rather than `money()`'s own `$-12.50`. */
export function weekCaption(week: WeekSpendPoint): string {
  const suffix = week.partial ? " · partial week" : "";
  const amount = week.spending < 0 ? `−${money(Math.abs(week.spending))}` : money(week.spending);
  return `${weekRangeLabel(week.week_start)} · ${amount}${suffix}`;
}

/**
 * Inserts thousands separators on top of `money()`'s cents/whole-dollar
 * trimming. The shared `money()` used everywhere else in the app (delivery,
 * ride, and social amounts — all comfortably sub-$1,000) is left untouched;
 * only the whole-account bank total in `trackedShareSentence` runs large
 * enough to need grouping.
 */
function moneyGrouped(amount: number): string {
  const formatted = money(amount);
  const match = formatted.match(/^\$(\d+)(\.\d+)?$/);
  if (!match) return formatted;
  const [, wholePart, decimalPart = ""] = match;
  const grouped = wholePart.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `$${grouped}${decimalPart}`;
}

/** "Of that, the things you're tracking" sentence (spec §5.4): the tracked
 * (delivery/rides/social) share of the whole-account bank total, stated as
 * prose rather than a gauge. `spent === 0` means there is nothing to be a
 * share of, so the sentence is suppressed entirely rather than reading
 * "$0 of the $0 above." */
export function trackedShareSentence(tracked: number, spent: number): string {
  if (spent === 0) return "";
  return `Delivery, rides and social are ${moneyGrouped(tracked)} of the ${moneyGrouped(spent)} above.`;
}

interface SubtotalDelivery { service: string; amount: number | null }
interface SubtotalRide { service: string; amount: number | null; user_is_work: boolean | null }
interface SubtotalSocialEvent { amount: number | null; end_at?: string }
interface SubtotalDay {
  deliveries: SubtotalDelivery[];
  rides: SubtotalRide[];
  social_events: SubtotalSocialEvent[];
}
export interface SpendRow { kind: string; service: string; amount: number }

/**
 * Client-side mirror of the backend's spend_by_service shape, computed from a
 * single day's /today payload. Null amounts are skipped (nothing to sum), a
 * confirmed work ride (user_is_work === true) is excluded — matching the
 * backend's _personal_rides rule, an AI flag alone never excludes a ride —
 * and social events collapse into one "Social" row regardless of source. A
 * social event whose end_at hasn't happened yet is excluded from the total
 * (it still shows under "Noticed quietly") so "Spent today" agrees with
 * Week, which gates social spend on the event having occurred.
 */
export function subtotalsFromDay(day: SubtotalDay, now: number = Date.now()): SpendRow[] {
  const sums = new Map<string, SpendRow>();
  const add = (kind: string, service: string, amount: number | null) => {
    if (amount == null) return;
    const key = `${kind}:${service}`;
    const existing = sums.get(key);
    if (existing) existing.amount += amount;
    else sums.set(key, { kind, service, amount });
  };

  for (const d of day.deliveries) add("delivery", d.service, d.amount);
  for (const r of day.rides) {
    if (r.user_is_work) continue;
    add("ride", r.service, r.amount);
  }
  for (const e of day.social_events) {
    if (e.end_at !== undefined && new Date(e.end_at).getTime() > now) continue;
    add("social", "Social", e.amount);
  }

  return Array.from(sums.values())
    .map((r) => ({ ...r, amount: Math.round(r.amount * 100) / 100 }))
    .sort((a, b) => b.amount - a.amount);
}

export interface DayItem {
  kind: "delivery" | "ride" | "social";
  service: string;
  label: string;
  at: string;
  amount: number;
  is_work: boolean;
}

export interface Day {
  date: string;
  gym: boolean;
  alcohol_level: number | null;
  substances: boolean;
  total: number;
  items: DayItem[];
}

export interface Chip { label: string; tone: "accent" | "over" }

/** Splits a date into the two lines a week-day row's date button shows:
 * a 3-letter weekday ("Mon") over an abbreviated month + day ("Jul 20"). */
export function dayRowDate(iso: string): { weekday: string; monthDay: string } {
  const d = parseDay(iso);
  return { weekday: DAYS[d.getDay()].slice(0, 3), monthDay: `${MONTHS[d.getMonth()]} ${d.getDate()}` };
}

/**
 * Chips summarise a day, they do not enumerate it — one chip per category, not
 * one per item. Ceiling metrics (delivery, alcohol, substances) tint `over`;
 * floor metrics and rides tint `accent`. Work rides never produce a chip of
 * their own — they only surface in the expanded panel — so the ride count
 * here is personal rides only.
 */
export function dayChips(day: Day): Chip[] {
  const chips: Chip[] = [];
  if (day.gym) chips.push({ label: "Gym", tone: "accent" });
  if (day.items.some((i) => i.kind === "social")) chips.push({ label: "Social", tone: "accent" });
  if (day.alcohol_level != null) chips.push({ label: `Alcohol ${day.alcohol_level}`, tone: "over" });
  if (day.substances) chips.push({ label: "Substances", tone: "over" });

  const deliveryCount = day.items.filter((i) => i.kind === "delivery").length;
  if (deliveryCount > 0) chips.push({ label: `${deliveryCount} delivery`, tone: "over" });

  const rideCount = day.items.filter((i) => i.kind === "ride" && !i.is_work).length;
  if (rideCount > 0) chips.push({ label: `${rideCount} ride${rideCount === 1 ? "" : "s"}`, tone: "accent" });

  return chips;
}

/**
 * Diff the editor's current fields against what was loaded and return only the
 * fields the user actually changed — so PATCH never manufactures an override
 * (a pinned title, a bogus is_social flip) the user never made. An emptied cost
 * field becomes an explicit `null` (clears a stored amount) rather than being
 * omitted (which would leave a stale amount untouched).
 */
export function buildSocialPatch(state: SocialEditState): SocialPatch {
  const patch: SocialPatch = {};

  const trimmedTitle = state.title.trim();
  if (trimmedTitle !== state.loadedTitle) patch.title = trimmedTitle;

  if (state.isSocial !== state.loadedIsSocial) patch.is_social = state.isSocial;

  const amountText = state.amountText.trim();
  if (amountText === "") {
    if (state.loadedAmount !== null) patch.amount = null;
  } else {
    const amount = Number(amountText);
    if (amount !== state.loadedAmount) patch.amount = amount;
  }

  return patch;
}
