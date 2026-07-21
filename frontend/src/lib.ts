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
