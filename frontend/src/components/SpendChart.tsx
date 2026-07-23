import { money, weekRangeLabel } from "../lib";

export interface SpendWeekPoint { week_start: string; delivery: number; rides: number; social: number }
interface Props {
  weeks: SpendWeekPoint[];
  onSelect?: (index: number) => void;
}

const VB_W = 360, VB_H = 96;
const PLOT_TOP = 8, BASELINE = 64;
const PLOT_H = BASELINE - PLOT_TOP;
const PLOT_LEFT = 2, PLOT_RIGHT = VB_W - 2;
const AXIS_Y = 80;
const SEG_GAP = 2;

// Stack order bottom → top: delivery, rides, social.
const CATS: { key: keyof Omit<SpendWeekPoint, "week_start">; cls: string }[] = [
  { key: "delivery", cls: "spend-seg-delivery" },
  { key: "rides", cls: "spend-seg-rides" },
  { key: "social", cls: "spend-seg-social" },
];

export default function SpendChart({ weeks, onSelect }: Props) {
  if (weeks.length === 0) return null;

  const totals = weeks.map((w) => w.delivery + w.rides + w.social);
  const max = Math.max(...totals, 1) * 1.15; // headroom so the tallest bar isn't flush with the top
  const bw = (PLOT_RIGHT - PLOT_LEFT) / weeks.length;
  const lastIndex = weeks.length - 1;

  return (
    <svg
      className="spend-chart"
      viewBox={`0 0 ${VB_W} ${VB_H}`}
      role="img"
      aria-label={`Weekly spend for the last ${weeks.length} weeks, by category`}
      preserveAspectRatio="xMidYMid meet"
    >
      <line className="spend-baseline" x1={PLOT_LEFT} x2={PLOT_RIGHT} y1={BASELINE} y2={BASELINE} />

      {weeks.map((w, i) => {
        const colX = PLOT_LEFT + i * bw;
        const barX = colX + 1;
        const barWidth = Math.max(bw - 2, 1);
        const total = totals[i];
        const title = `${weekRangeLabel(w.week_start)} · ${money(total)}`;

        let y = BASELINE;
        const segments = CATS.map(({ key, cls }) => {
          const value = w[key];
          if (value <= 0) return null;
          const h = Math.max((value / max) * PLOT_H, 1);
          const segY = y - h;
          y = segY - SEG_GAP;
          return { cls, segY, h, value };
        }).filter((s): s is NonNullable<typeof s> => s !== null);

        return (
          <g key={w.week_start}>
            {segments.map((s) => (
              <rect key={s.cls} className={`spend-seg ${s.cls}`} x={barX} y={s.segY} width={barWidth} height={s.h} />
            ))}
            <rect
              className="spend-hit"
              x={colX} y={0} width={bw} height={VB_H}
              tabIndex={onSelect ? 0 : -1}
              role={onSelect ? "button" : undefined}
              aria-label={title}
              onClick={() => onSelect?.(i)}
              onKeyDown={(e) => {
                if (!onSelect) return;
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onSelect(i);
                }
              }}
            >
              <title>{title}</title>
            </rect>
          </g>
        );
      })}

      <text className="spend-axis" x={PLOT_LEFT} y={AXIS_Y} textAnchor="start">
        {weekRangeLabel(weeks[0].week_start)}
      </text>
      <text className="spend-axis" x={PLOT_RIGHT} y={AXIS_Y} textAnchor="end">
        {weekRangeLabel(weeks[lastIndex].week_start)}
      </text>
    </svg>
  );
}
