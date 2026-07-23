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
