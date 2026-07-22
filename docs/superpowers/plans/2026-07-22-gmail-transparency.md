# Gmail Transparency + 30-Day Backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Configurable Gmail scan lookback (for a 30-day backfill), a scan-result summary in Settings, and an auditable list of detected delivery orders.

**Architecture:** One new env var flows `config.py` → `gmail_service` query. The scan job stores its existing counters as a `gmail_last_result` setting. A thin `GET /api/deliveries` route reuses `db.get_delivery_orders_range`. Settings screen renders both.

**Tech Stack:** FastAPI, React + TypeScript. No new tables, no new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-22-gmail-transparency-design.md`

## Global Constraints

- `config.py` is the only env reader; `database.py` the only SQL location (reuse `get_delivery_orders_range` — no new SQL).
- `gmail_last_result` format exactly: `"{candidates} candidates · {ai_checked} AI-checked · {added} new orders"`; written only on scan success; untouched on failure.
- `/api/deliveries` `days` clamped 1–365, default 60; response `{"orders": [{service, subject, ordered_at}, ...]}` newest-first, no other keys per order.
- Frontend: only existing OKLCH tokens; native `<details>` for the collapsible section.
- Backend tests `pytest tests/ -v`; frontend `cd frontend && npm test -- --run && npm run build`. No commits with failing checks.

---

### Task 1: Backend — lookback config, scan summary, deliveries route

**Files:**
- Modify: `config.py`
- Modify: `.env.example`
- Modify: `services/gmail_service.py`
- Modify: `jobs/scan_gmail.py`
- Modify: `app/routes.py` (settings route + new deliveries route)
- Test: `tests/test_scan_gmail.py`, `tests/test_api_routes.py`

**Interfaces:**
- Produces: `config.GMAIL_SCAN_LOOKBACK_DAYS` (int, default 7); `gmail_service._query() -> str`; setting key `gmail_last_result`; `GET /api/settings` gains `gmail_last_result`; `GET /api/deliveries?days=60` → `{"orders": [{"service": str, "subject": str, "ordered_at": str}, ...]}` newest-first.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scan_gmail.py` (follow the file's existing mocking pattern for `google_auth.is_configured` and `fetch_delivery_candidates` — read it first and adapt these tests to its fixtures/monkeypatch style):

```python
def test_query_uses_lookback_default():
    from services import gmail_service
    assert "newer_than:7d" in gmail_service._query()
    assert "from:(" in gmail_service._query()


def test_scan_writes_last_result(temp_db_path, mock_anthropic, monkeypatch):
    import database as db
    import jobs.scan_gmail as job
    monkeypatch.setattr(job.google_auth, "is_configured", lambda: True)
    monkeypatch.setattr(job, "fetch_delivery_candidates", lambda: [
        {"gmail_message_id": "m1", "sender": "noreply@doordash.com",
         "subject": "Order Confirmation for Tom", "ordered_at": "2026-07-20T18:00:00"},
    ])
    job.run()
    assert db.get_setting("gmail_last_status") == "ok"
    result = db.get_setting("gmail_last_result")
    assert result is not None
    assert result.startswith("1 candidates")
    assert "new orders" in result
```

Append to `tests/test_api_routes.py`:

```python
def test_deliveries_list_shape_and_order(temp_db_path):
    client = _client(temp_db_path)
    import database as db
    d1 = (datetime.date.today() - datetime.timedelta(days=10)).isoformat()
    d2 = (datetime.date.today() - datetime.timedelta(days=3)).isoformat()
    db.add_delivery_order("m1", "DoorDash", f"{d1}T18:00:00", "Your order")
    db.add_delivery_order("m2", "Uber Eats", f"{d2}T19:30:00", "Your receipt")
    body = client.get("/api/deliveries").json()
    assert [o["service"] for o in body["orders"]] == ["Uber Eats", "DoorDash"]
    assert set(body["orders"][0].keys()) == {"service", "subject", "ordered_at"}
    # days clamp: 0 -> 1; a 1-day window excludes both seeded orders
    assert client.get("/api/deliveries?days=0").json()["orders"] == []
    # settings gains the result field (None when never written)
    assert "gmail_last_result" in client.get("/api/settings").json()
```

(Note: `db.add_delivery_order` returns whether the row was inserted; use it directly. `datetime` is already imported in this file.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scan_gmail.py tests/test_api_routes.py -v -k "lookback or last_result or deliveries"`
Expected: FAIL — `_query` missing, `gmail_last_result` absent, `/deliveries` 404.

- [ ] **Step 3: Implement**

`config.py` — after `GMAIL_SCAN_INTERVAL_HOURS`:

```python
GMAIL_SCAN_LOOKBACK_DAYS = int(os.getenv("GMAIL_SCAN_LOOKBACK_DAYS", "7"))
```

`.env.example` — add under the job-schedule section:

```
GMAIL_SCAN_LOOKBACK_DAYS=7
```

`services/gmail_service.py` — replace the module-level `QUERY` constant:

```python
from config import GMAIL_SCAN_LOOKBACK_DAYS, TIMEZONE

_SENDERS = "from:(" + " OR ".join(DELIVERY_DOMAINS) + ")"


def _query() -> str:
    return f"{_SENDERS} newer_than:{GMAIL_SCAN_LOOKBACK_DAYS}d"
```

In `fetch_delivery_candidates`, change `q=QUERY` to `q=_query()` and update the docstring's "last 7 days" to "lookback window (GMAIL_SCAN_LOOKBACK_DAYS)".

`jobs/scan_gmail.py` — in the success path, alongside the existing `set_setting` calls:

```python
        db.set_setting("gmail_last_run", _now_iso())
        db.set_setting("gmail_last_status", "ok")
        db.set_setting(
            "gmail_last_result",
            f"{len(candidates)} candidates · {ai_checked} AI-checked · {added} new orders",
        )
```

The failure path is unchanged (`gmail_last_result` keeps describing the last successful scan).

`app/routes.py` — in `get_settings()`, add after `"gmail_last_status"`:

```python
        "gmail_last_result": db.get_setting("gmail_last_result"),
```

New route (after `/settings`):

```python
@router.get("/deliveries")
def get_deliveries(days: int = 60):
    d = min(max(days, 1), 365)
    end = _local_today()
    start = end - datetime.timedelta(days=d)
    orders = db.get_delivery_orders_range(start.isoformat(), end.isoformat())
    orders.sort(key=lambda o: o["ordered_at"], reverse=True)
    return {"orders": [
        {"service": o["service"], "subject": o["subject"], "ordered_at": o["ordered_at"]}
        for o in orders
    ]}
```

- [ ] **Step 4: Run the full backend suite**

Run: `pytest tests/ -v`
Expected: all PASS (existing scan tests unaffected — `gmail_last_result` is additive).

- [ ] **Step 5: Update docs**

In `CLAUDE.md`'s optional env-var table, add a row:

```
| `GMAIL_SCAN_LOOKBACK_DAYS` | Gmail scan lookback window in days (default 7) |
```

- [ ] **Step 6: Commit**

```bash
git add config.py .env.example services/gmail_service.py jobs/scan_gmail.py app/routes.py tests/test_scan_gmail.py tests/test_api_routes.py CLAUDE.md
git commit -m "feat(api): configurable gmail lookback, scan-result summary, /deliveries audit route"
```

---

### Task 2: Settings UI — scan summary + detected-orders list

**Files:**
- Modify: `frontend/src/screens/Settings.tsx`
- Modify: `frontend/src/styles.css` (append)

**Interfaces:**
- Consumes: Task 1's `gmail_last_result` field on `/settings` and `GET /deliveries?days=60`.

- [ ] **Step 1: Update `Settings.tsx`**

Add to `SettingsData`:

```tsx
  gmail_last_result: string | null;
```

Add below the existing interfaces:

```tsx
interface Delivery { service: string; subject: string; ordered_at: string }

function orderDate(iso: string): string {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "" : d.toLocaleDateString([], { month: "short", day: "numeric" });
}
```

Add state + fetch (alongside the existing ones; a failed fetch hides the section):

```tsx
  const [deliveries, setDeliveries] = useState<Delivery[] | null>(null);
```

```tsx
    apiGet<{ orders: Delivery[] }>("/deliveries?days=60")
      .then((r) => setDeliveries(r.orders))
      .catch(() => setDeliveries(null));
```

Gmail row hint — append the result when present:

```tsx
            <span className="hint">
              {statusLine(settings.gmail_last_status, settings.gmail_last_run)}
              {settings.gmail_last_result ? ` · ${settings.gmail_last_result}` : ""}
            </span>
```

After the Sync `</div>` (end of the group), add:

```tsx
      {deliveries && (
        <>
          <p className="section-label">Detected orders</p>
          <details className="orders">
            <summary>
              {deliveries.length === 0
                ? "None detected yet"
                : `${deliveries.length} in the last 60 days`}
            </summary>
            {deliveries.map((o) => (
              <p className="quiet" key={o.ordered_at + o.subject}>
                <span>{o.service} — {o.subject}</span>
                <span className="when">{orderDate(o.ordered_at)}</span>
              </p>
            ))}
          </details>
        </>
      )}
```

- [ ] **Step 2: Append CSS to `frontend/src/styles.css`**

```css
/* ── Detected orders (Settings) ── */
details.orders {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 12px 14px;
}
details.orders summary { cursor: pointer; font-size: 14px; color: var(--ink-2); }
details.orders .quiet { margin: 10px 0 0; }
```

- [ ] **Step 3: Verify**

Run: `cd frontend && npm test -- --run && npm run build`
Expected: tests pass, build clean.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/screens/Settings.tsx frontend/src/styles.css
git commit -m "feat(frontend): scan summary and detected-orders audit in settings"
```

---

### Task 3 (ops, run by controller after deploy)

- [ ] `railway variables --set GMAIL_SCAN_LOOKBACK_DAYS=30`
- [ ] Merge to main, push (auto-deploys); the startup scan backfills the last 30 days.
- [ ] Verify in logs: `Gmail scan: N candidates, ...` with N covering the month; spot-check the Detected-orders list in Settings.
