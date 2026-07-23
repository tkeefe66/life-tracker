import { useState } from "react";
import { dayRowDate, money, type TriageChoice } from "../lib";

/** One row of the triage worklist (spec §6.2) — the shape `app/money.py`'s
 * `_decorate_bucket` returns for both the `ambiguous` and `inflow_unknown`
 * buckets. `amount` is signed (negative = money out, positive = money in);
 * this component always displays its magnitude — the queue's own heading
 * ("Spent it, or moved it?" vs "Where did this come from?") already carries
 * direction, and `money()` isn't built to print a leading minus sign. */
export interface TriageRow {
  simplefin_id: string;
  posted: string;
  amount: number;
  payee: string | null;
  description: string | null;
  label: string;
  account_name: string;
  resolved_flow: string;
  user_flow: string | null;
  signature: string;
  signature_count: number;
  signature_amount: number;
}

interface Props {
  title: string;
  prompt: string;
  rows: TriageRow[];
  choices: TriageChoice[];
  onAnswer: (id: string, flow: string) => void;
  onBulk: (ids: string[], flow: string) => void;
}

/**
 * A "just answered" row, kept purely to decide whether to show ONE bulk-offer
 * line beneath it. Whether a row is answered in the persisted sense — and
 * whether it's still in `rows` at all — is the PARENT's concern: Task 8
 * removes answered rows from the array it passes in (optimistically, ahead
 * of the request resolving). This component never removes a row itself; it
 * only remembers enough, for the moment the row is still on screen, to offer
 * "apply to the others" once.
 */
interface JustAnswered {
  id: string;
  flow: string;
}

export default function TriageQueue({ title, prompt, rows, choices, onAnswer, onBulk }: Props) {
  const [justAnswered, setJustAnswered] = useState<JustAnswered | null>(null);

  if (rows.length === 0) {
    return (
      <section className="triage-queue">
        <h3>{title}</h3>
        <p className="quiet empty">Nothing to sort out.</p>
      </section>
    );
  }

  const handleAnswer = (row: TriageRow, flow: string) => {
    setJustAnswered({ id: row.simplefin_id, flow });
    onAnswer(row.simplefin_id, flow);
  };

  const handleBulk = (row: TriageRow, flow: string) => {
    const ids = rows
      .filter((r) => r.simplefin_id !== row.simplefin_id && r.signature === row.signature)
      .map((r) => r.simplefin_id);
    onBulk(ids, flow);
  };

  return (
    <section className="triage-queue">
      <h3>{title}</h3>
      <p className="triage-prompt">{prompt}</p>
      <ul className="triage-list">
        {rows.map((row) => {
          const answeredWith = justAnswered?.id === row.simplefin_id ? justAnswered : null;
          const { monthDay } = dayRowDate(row.posted);

          return (
            <li key={row.simplefin_id} className={`triage-row${answeredWith ? " settle" : ""}`}>
              <div className="triage-row-top">
                <span className="triage-detail">
                  <span className="triage-label">{row.label}</span>
                  <span className="triage-meta">{row.account_name} · {monthDay}</span>
                </span>
                <span className="triage-amount num">{money(Math.abs(row.amount))}</span>
              </div>

              <div className="chips">
                {choices.map((c) => (
                  <button key={c.flow} onClick={() => handleAnswer(row, c.flow)}>
                    {c.label}
                  </button>
                ))}
              </div>

              {answeredWith && row.signature_count >= 2 && (
                <button className="quiet-btn" onClick={() => handleBulk(row, answeredWith.flow)}>
                  Apply to the other {row.signature_count} {row.signature} charges · {money(row.signature_amount)}
                </button>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
