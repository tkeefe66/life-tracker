import { money, serviceLabel, type SpendRow } from "../lib";

interface Props {
  rows: SpendRow[];
  title?: string;
}

/** One line per service (label left, amount right) plus a Total line. Renders
 * nothing when there is no spend at all — a quiet screen stays quiet. */
export default function SpendSubtotals({ rows, title }: Props) {
  if (rows.length === 0 || rows.every((r) => r.amount === 0)) return null;
  const total = rows.reduce((sum, r) => sum + r.amount, 0);

  return (
    <>
      {title && <p className="section-label">{title}</p>}
      <div className="spend">
        {rows.map((r) => (
          <div className="spend-row" key={`${r.kind}:${r.service}`}>
            <span className="spend-service">{serviceLabel(r.kind, r.service)}</span>
            <span className="spend-amount num">{money(r.amount)}</span>
          </div>
        ))}
        <div className="spend-row spend-total">
          <span>Total</span>
          <span className="num">{money(total)}</span>
        </div>
      </div>
    </>
  );
}
