import type { CSSProperties } from "react";

const DAY_LETTERS = ["M", "T", "W", "T", "F", "S", "S"];

interface Row { label: string; counts: number[]; caution: boolean }

export default function WeekdayHeatmap({ rows }: { rows: Row[] }) {
  return (
    <div className="heatmap">
      <div className="hm-row">
        <span className="hm-label" />
        {DAY_LETTERS.map((d, i) => (
          <span key={i} className="hm-day">{d}</span>
        ))}
      </div>
      {rows.map((r) => {
        const max = Math.max(...r.counts, 1);
        return (
          <div className="hm-row" key={r.label}>
            <span className="hm-label">{r.label}</span>
            {r.counts.map((c, i) => (
              <span key={i}
                className={`hm-cell${r.caution ? " over" : ""}`}
                style={{ "--pct": `${Math.round((c / max) * 100)}%` } as CSSProperties}
                title={`${c}`} />
            ))}
          </div>
        );
      })}
    </div>
  );
}
