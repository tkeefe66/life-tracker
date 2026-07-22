import { weekLabel } from "../lib";

interface Props {
  weekStart: string;
  isCurrent: boolean;
  onPrev: () => void;
  onNext: () => void;
}

export default function WeekNav({ weekStart, isCurrent, onPrev, onNext }: Props) {
  return (
    <div className={`navhead${isCurrent ? "" : " past"}`}>
      <button className="nav-btn" aria-label="Previous week" onClick={onPrev}>‹</button>
      <div className="nav-label">
        <h2>{isCurrent ? "This week" : "Week of"}</h2>
        <p className="sub">{weekLabel(weekStart)}</p>
      </div>
      <button className="nav-btn" aria-label="Next week" onClick={onNext} disabled={isCurrent}>›</button>
    </div>
  );
}
