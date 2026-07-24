import { useEffect, useState } from "react";
import { apiGet } from "../api";
import { dayRowDate, flowLabel, money, vendorSplit, type VendorLine } from "../lib";

interface AccountRow { id: number; name: string; active: boolean }
interface DrillRow {
  simplefin_id: string;
  posted: string;
  amount: number;
  account_name: string;
  resolved_flow: string;
  user_note: string | null;
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
    apiGet<{ lines: VendorLine[] }>(`/bank/breakdown?weeks=${weeks}${acct}`)
      .then((d) => setLines(d.lines))
      .catch(() => setLines(null));
  }, [weeks, accountId]);

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
      apiGet<{ rows: DrillRow[] }>(
        `/bank/breakdown/rows?weeks=${weeks}&payee=${encodeURIComponent(vendor)}${acct}`,
      )
        .then((d) => setDrill((prev) => ({ ...prev, [vendor]: d.rows })))
        .catch(() => {});
    }
  };

  return (
    <>
      <p className="section-label">Where it went</p>
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
        {shown.map((l) => (
          <div key={l.vendor}>
            <button type="button" className="vendor-row" onClick={() => toggleVendor(l.vendor)}>
              <span className="spend-service">{l.vendor}</span>
              <span className="spend-amount num">
                {l.count > 0 ? `${l.count} · ` : ""}{money(l.amount)}
              </span>
            </button>
            {expanded === l.vendor && drill[l.vendor] && (
              <div className="vendor-drill">
                {drill[l.vendor].map((r) => (
                  <div className="vendor-drill-row" key={r.simplefin_id}>
                    <span>
                      {dayRowDate(r.posted.slice(0, 10)).monthDay}
                      {r.resolved_flow === "refund" && ` · ${flowLabel("refund")}`}
                      {" · "}{r.account_name}
                      {r.user_note && <span className="triage-note">{r.user_note}</span>}
                    </span>
                    <span className="num">{money(Math.abs(r.amount))}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
        {tail && !showRest && (
          <button type="button" className="vendor-row" onClick={() => setShowRest(true)}>
            <span className="spend-service">Everything else ({tail.vendors} vendors)</span>
            <span className="spend-amount num">{tail.count} · {money(tail.amount)}</span>
          </button>
        )}
      </div>
    </>
  );
}
