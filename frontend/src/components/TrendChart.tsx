interface Point { count: number; hit: boolean }
interface Props { points: Point[]; target: number }

const W = 240, H = 56, PAD = 2, TOP = 8;

export default function TrendChart({ points, target }: Props) {
  if (points.length === 0) return null;
  const max = Math.max(target, ...points.map((p) => p.count), 1);
  const bw = (W - PAD * 2) / points.length;
  const y = (v: number) => H - (v / max) * (H - TOP);
  return (
    <svg className="trend" viewBox={`0 0 ${W} ${H}`} role="img"
      aria-label={`Weekly counts, last ${points.length} weeks`}>
      {points.map((p, i) => (
        <rect key={i} className={p.hit ? "hit" : "miss"}
          x={PAD + i * bw + 1} y={y(p.count)}
          width={Math.max(bw - 2, 1)} height={Math.max(H - y(p.count), p.count > 0 ? 2 : 0)}
          rx="1.5" />
      ))}
      <line className="target" x1={0} x2={W} y1={y(target)} y2={y(target)} />
    </svg>
  );
}
