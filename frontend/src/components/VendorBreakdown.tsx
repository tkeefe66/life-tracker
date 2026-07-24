import { useEffect, useRef, useState } from "react";
import { apiGet, apiSend } from "../api";
import { dayRowDate, flowLabel, money, signedMoney, vendorSplit, type VendorLine } from "../lib";

interface AccountRow { id: number; name: string; active: boolean }
interface DrillRow {
  simplefin_id: string;
  posted: string;
  amount: number;
  account_name: string;
  resolved_flow: string;
  user_note: string | null;
  user_label: string | null;
}

// "Where it went" — bank spending grouped by vendor, filterable by account,
// with a per-vendor transaction drill-down. Secondary surface: any failed
// fetch hides the section (lines === null), never the screen.
export default function VendorBreakdown({ weeks }: { weeks: number }) {
  const [lines, setLines] = useState<VendorLine[] | null>(null);
  const [accounts, setAccounts] = useState<AccountRow[]>([]);
  const [accountId, setAccountId] = useState<number | null>(null);
  const [showRest, setShowRest] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [drill, setDrill] = useState<Record<string, DrillRow[]>>({});
  const [vocab, setVocab] = useState<string[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  // Set right before the Escape-triggered setEditing(null) so the input's
  // onBlur (fired by React unmounting the now-unfocused input) can tell a
  // deliberate cancel apart from a normal blur-to-save and skip saveLabel.
  const cancelingRef = useRef(false);
  // Bumped on every accountId/weeks change; fetch closures capture the value
  // at call time and discard their response if it no longer matches, so a
  // stale in-flight lines or drill fetch can never repopulate state after a
  // newer request has superseded it.
  const fetchGen = useRef(0);

  useEffect(() => {
    apiGet<AccountRow[]>("/bank/accounts")
      .then((rows) => setAccounts(rows.filter((a) => a.active)))
      .catch(() => setAccounts([]));
  }, []);

  useEffect(() => {
    const acct = accountId !== null ? `&account_id=${accountId}` : "";
    setExpanded(null);
    setDrill({});
    setShowRest(false);
    fetchGen.current += 1;
    const gen = fetchGen.current;
    apiGet<{ lines: VendorLine[]; labels: string[] }>(`/bank/breakdown?weeks=${weeks}${acct}`)
      .then((d) => {
        if (fetchGen.current !== gen) return;
        setLines(d.lines);
        setVocab(d.labels ?? []);
      })
      .catch(() => {
        if (fetchGen.current !== gen) return;
        setLines(null);
      });
  }, [weeks, accountId]);

  const saveLabel = (drillKey: string, row: DrillRow, raw: string) => {
    const label = raw.trim() || null;
    setEditing(null);
    if (label === row.user_label) return;
    const gen = fetchGen.current;
    apiSend("POST", "/bank/label", { simplefin_id: row.simplefin_id, label })
      .then(() => {
        if (fetchGen.current !== gen) return;
        setDrill((prev) => ({
          ...prev,
          [drillKey]: (prev[drillKey] ?? []).map((r) =>
            r.simplefin_id === row.simplefin_id ? { ...r, user_label: label } : r),
        }));
        if (label && !vocab.includes(label)) {
          setVocab((v) => [...v, label].sort());
        }
      })
      .catch(() => {});
  };

  if (!lines || lines.length === 0) return null;

  const { top, tail, rest } = vendorSplit(lines);
  const shown = showRest ? [...top, ...rest] : top;

  const toggleVendor = (vendor: string) => {
    if (expanded === vendor) {
      setExpanded(null);
      return;
    }
    setExpanded(vendor);
    if (!drill[vendor]) {
      const acct = accountId !== null ? `&account_id=${accountId}` : "";
      const gen = fetchGen.current;
      apiGet<{ rows: DrillRow[] }>(
        `/bank/breakdown/rows?weeks=${weeks}&payee=${encodeURIComponent(vendor)}${acct}`,
      )
        .then((d) => {
          if (fetchGen.current !== gen) return;
          setDrill((prev) => ({ ...prev, [vendor]: d.rows }));
        })
        .catch(() => {});
    }
  };

  return (
    <>
      <p className="section-label">Where it went</p>
      <datalist id="vendor-label-vocab">
        {vocab.map((l) => <option key={l} value={l} />)}
      </datalist>
      {accounts.length > 1 && (
        <div className="vendor-chip-row">
          <button
            type="button"
            className={accountId === null ? "vendor-chip vendor-chip-on" : "vendor-chip"}
            onClick={() => setAccountId(null)}
          >
            All accounts
          </button>
          {accounts.map((a) => (
            <button
              key={a.id}
              type="button"
              className={accountId === a.id ? "vendor-chip vendor-chip-on" : "vendor-chip"}
              onClick={() => setAccountId((prev) => (prev === a.id ? null : a.id))}
            >
              {a.name}
            </button>
          ))}
        </div>
      )}
      <div className="spend">
        {shown.map((l, i) => {
          const drillId = `vendor-drill-${i}`;
          return (
            <div key={l.vendor}>
              <button
                type="button"
                className="vendor-row"
                aria-expanded={expanded === l.vendor}
                aria-controls={drillId}
                onClick={() => toggleVendor(l.vendor)}
              >
                <span className="spend-service">{l.vendor}</span>
                <span className="spend-amount num">
                  {l.count > 0 ? `${l.count} · ` : ""}{signedMoney(l.amount)}
                </span>
              </button>
              {expanded === l.vendor && drill[l.vendor] && (
                <div className="vendor-drill" id={drillId}>
                  {drill[l.vendor].map((r) => (
                    <div className="vendor-drill-row" key={r.simplefin_id}>
                      <span>
                        {dayRowDate(r.posted.slice(0, 10)).monthDay}
                        {r.resolved_flow === "refund" && ` · ${flowLabel("refund")}`}
                        {" · "}{r.account_name}
                        {r.user_note && <span className="triage-note">{r.user_note}</span>}
                      </span>
                      <span className="vendor-drill-right">
                        {editing === r.simplefin_id ? (
                          <input
                            className="vendor-label-input"
                            list="vendor-label-vocab"
                            aria-label="Label"
                            defaultValue={r.user_label ?? ""}
                            autoFocus
                            onBlur={(e) => {
                              if (cancelingRef.current) {
                                cancelingRef.current = false;
                                return;
                              }
                              saveLabel(l.vendor, r, e.currentTarget.value);
                            }}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") e.currentTarget.blur();
                              if (e.key === "Escape") {
                                cancelingRef.current = true;
                                setEditing(null);
                              }
                            }}
                          />
                        ) : (
                          <button
                            type="button"
                            className="vendor-label-btn"
                            onClick={() => setEditing(r.simplefin_id)}
                          >
                            {r.user_label ?? "＋ label"}
                          </button>
                        )}
                        <span className="num">{money(Math.abs(r.amount))}</span>
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
        {tail && !showRest && (
          <button
            type="button"
            className="vendor-row"
            aria-expanded={showRest}
            onClick={() => setShowRest(true)}
          >
            <span className="spend-service">Everything else ({tail.vendors} vendors)</span>
            <span className="spend-amount num">{tail.count} · {signedMoney(tail.amount)}</span>
          </button>
        )}
      </div>
    </>
  );
}
