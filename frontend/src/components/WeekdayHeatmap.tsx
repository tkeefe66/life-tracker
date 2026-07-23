import { useState } from "react";

const DAY_LETTERS = ["M", "T", "W", "T", "F", "S", "S"];
const DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const LEGEND_STEPS = [25, 62, 100];

interface Row { label: string; counts: number[]; caution: boolean }
interface Selection { rowIndex: number; dayIndex: number }

function cellBackground(count: number, max: number, caution: boolean): string {
  if (count <= 0 || max <= 0) return "var(--surface-2)";
  const pct = 25 + (count / max) * 75;
  const color = caution ? "var(--chart-over)" : "var(--chart-hit)";
  return `color-mix(in oklch, ${color} ${pct}%, var(--surface-2))`;
}

export default function WeekdayHeatmap({ rows }: { rows: Row[] }) {
  const [selected, setSelected] = useState<Selection | null>(null);

  return (
    <div className="heatmap">
      <div className="hm-row">
        <span className="hm-label" />
        {DAY_LETTERS.map((d, i) => (
          <span key={i} className="hm-day">{d}</span>
        ))}
      </div>
      {rows.map((r, rowIndex) => {
        const max = Math.max(...r.counts, 1);
        return (
          <div className="hm-row" key={r.label}>
            <span className="hm-label">
              {r.label} <span className="hm-max">max {max}</span>
            </span>
            {r.counts.map((c, dayIndex) => {
              const title = `${r.label} · ${DAY_NAMES[dayIndex]}s: ${c}`;
              return (
                <button
                  key={dayIndex}
                  type="button"
                  className="hm-cell"
                  style={{ background: cellBackground(c, max, r.caution) }}
                  title={title}
                  aria-label={title}
                  onClick={() =>
                    setSelected((prev) =>
                      prev && prev.rowIndex === rowIndex && prev.dayIndex === dayIndex
                        ? null
                        : { rowIndex, dayIndex }
                    )
                  }
                />
              );
            })}
          </div>
        );
      })}

      <div className="hm-legend">
        <span className="hm-legend-word">less</span>
        {LEGEND_STEPS.map((pct) => (
          <span
            key={pct}
            className="hm-swatch"
            style={{ background: `color-mix(in oklch, var(--chart-hit) ${pct}%, var(--surface-2))` }}
          />
        ))}
        <span className="hm-legend-word">more</span>
      </div>

      {selected && (
        <p className="hm-caption">
          {rows[selected.rowIndex].label} · {DAY_NAMES[selected.dayIndex]}s: {rows[selected.rowIndex].counts[selected.dayIndex]}
        </p>
      )}
    </div>
  );
}
