import { dayLabel, relativeDayLabel } from "../lib";

interface Props {
  date: string;
  todayIso: string;
  onPrev: () => void;
  onNext: () => void;
}

export default function DayNav({ date, todayIso, onPrev, onNext }: Props) {
  const isToday = date === todayIso;
  return (
    <div className={`navhead${isToday ? "" : " past"}`}>
      <button className="nav-btn" aria-label="Previous day" onClick={onPrev}>‹</button>
      <div className="nav-label">
        <h2>{relativeDayLabel(date, todayIso)}</h2>
        <p className="sub">{dayLabel(date)}</p>
      </div>
      <button className="nav-btn" aria-label="Next day" onClick={onNext} disabled={isToday}>›</button>
    </div>
  );
}
