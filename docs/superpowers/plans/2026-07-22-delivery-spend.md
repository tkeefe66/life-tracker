# Delivery Spend + Tip-Only Order Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development. Single implementer task; controller handles merge/deploy.

**Goal:** Count orders whose only email trace is a tip receipt (fixes the 5-vs-14 backfill undercount), extract the `Total $X` amount from every receipt, and surface weekly delivery spend in the app.

**Design summary:** An order is identified by the cluster key `(service, day, subject)` — Uber sends the order receipt, tip receipt, and refund adjustment for one order with the identical subject (daypart-specific, e.g. "Your Monday evening order with Uber Eats"), while distinct same-day orders get different dayparts. Follow-up emails update the existing cluster row's amount (tip/refund totals are the truer final spend); a tip receipt with no existing cluster row CREATES the order row (tip-only case); an order receipt arriving after a tip-created row is skipped (no double count).

## Global Constraints

- `database.py` only SQL; `config.py` only env; `ai_metrics.py` only Claude calls (unchanged this plan).
- New column needs a real migration for existing Postgres/SQLite DBs — do not rely on `CREATE TABLE IF NOT EXISTS`.
- Known accepted limitations: two same-service orders in the same daypart of one day collapse to one; cancelled orders still count; the 5 already-stored orders keep `NULL` amounts unless a follow-up email for their cluster is still unscanned.
- Backend tests `pytest tests/ -v`; frontend `cd frontend && npm test -- --run && npm run build`. No commits with failing checks. TDD: tests first per area.

## Task: implement across backend + frontend (one branch, commits per layer)

### 1. `receipts.py` (pure rules)

```python
_TIP_RE = re.compile(r"thanks for tipping", re.IGNORECASE)
_AMOUNT_RE = re.compile(r"Total \$([\d,]+(?:\.\d{2})?)")


def is_tip_receipt(snippet) -> bool:
    return bool(_TIP_RE.search(snippet or ""))


def extract_amount(snippet):
    """Order total from a receipt snippet, or None."""
    m = _AMOUNT_RE.search(snippet or "")
    return float(m.group(1).replace(",", "")) if m else None
```

Tests (`tests/test_receipts.py`): tip vs non-tip; amounts `"Total $16.31"` → 16.31, `"Total $1,024.50"` → 1024.5, `"Total $20"` → 20.0, no match → None, None input → None.

### 2. `database.py`

- Migration in `_init_v2_tables()` after the table creates:

```python
        if USE_POSTGRES:
            c.execute("ALTER TABLE delivery_orders ADD COLUMN IF NOT EXISTS amount REAL")
        else:
            cols = [r["name"] for r in c.execute("PRAGMA table_info(delivery_orders)").fetchall()]
            if "amount" not in cols:
                c.execute("ALTER TABLE delivery_orders ADD COLUMN amount REAL")
```

- `add_delivery_order(gmail_message_id, service, ordered_at, subject, amount=None)` — add the column to the INSERT; preserve the existing return semantics (truthy iff inserted).
- New `find_delivery_order(service, day, subject)` — row dict (`id`, `amount`) or None; match `service = ?`, `substr(ordered_at, 1, 10) = ?`, `subject = ?`.
- New `set_delivery_amount(order_id, amount)`.
- `get_delivery_orders_range` SELECT gains `amount` (and `id`).

Tests (`tests/test_database_v2.py`): roundtrip add-with-amount; find by cluster key hit/miss; set_delivery_amount updates; migration idempotent (call `initialize_db()` twice).

### 3. `jobs/scan_gmail.py` — candidate loop becomes:

```python
        for cand in candidates:
            if db.has_delivery_order(cand["gmail_message_id"]):
                continue
            snippet = cand.get("snippet", "")
            amount = receipts.extract_amount(snippet)
            day = cand["ordered_at"][:10]
            if receipts.is_followup(snippet):
                _, service = receipts.classify_candidate(cand["sender"], cand["subject"])
                if not service:
                    continue
                existing = db.find_delivery_order(service, day, cand["subject"])
                if existing:
                    if amount is not None:
                        db.set_delivery_amount(existing["id"], amount)
                elif receipts.is_tip_receipt(snippet):
                    if db.add_delivery_order(cand["gmail_message_id"], service,
                                             cand["ordered_at"], cand["subject"], amount):
                        added += 1
                continue
            verdict, service = receipts.classify_candidate(cand["sender"], cand["subject"])
            if verdict == "ambiguous":
                ai_checked += 1
                verdict = "order" if ai_metrics.classify_receipt(
                    cand["sender"], cand["subject"], snippet) else "not_order"
            if verdict == "order":
                if db.find_delivery_order(service, day, cand["subject"]):
                    continue
                if db.add_delivery_order(cand["gmail_message_id"], service,
                                         cand["ordered_at"], cand["subject"], amount):
                    added += 1
```

Tests (`tests/test_scan_gmail.py`), following the file's existing fixture style:
- tip-only candidate → one stored order carrying the tip amount, zero AI calls.
- order receipt then tip receipt (same subject/day) → one row, amount = tip total.
- tip first then order receipt (same subject/day) → one row, count 1.
- refund follow-up (`"adjusted the total"`, has `Total $`) after an order → amount updated to refund total.
- two orders, same service + day, different subjects (dayparts) → two rows.
- Existing tests must stay green (the triplet test's stored row will now carry the refund-adjusted amount — update its assertions if it checks amounts; it currently doesn't).

### 4. `app/scorecard.py` + `app/routes.py`

- `scorecard_for_week` adds `card["delivery_spend"] = round(sum(o["amount"] or 0 for o in orders), 2)` using `db.get_delivery_orders_range` over the week bounds.
- `/api/deliveries` response rows gain `"amount": o["amount"]` (may be null).

Tests (`tests/test_api_routes.py`): scorecard has `delivery_spend` summing seeded amounts (null amounts treated as 0); deliveries rows carry `amount`.

### 5. Frontend

- `frontend/src/screens/Scorecard.tsx`: `Card` interface gains `delivery_spend: number`. On the delivery metric row's `.m-sub` line, append `` · $X spent`` when `key === "delivery" && card.delivery_spend > 0` (format: `$${card.delivery_spend.toFixed(2).replace(/\.00$/, "")}`).
- `frontend/src/screens/Settings.tsx`: `Delivery` interface gains `amount: number | null`; each detected-order row appends `` · $X`` before the date when amount is non-null (same formatting).
- No new CSS needed.

Verify: `cd frontend && npm test -- --run && npm run build`.

### Commits

Layered commits with clear messages, e.g. `feat(receipts): tip/amount extraction rules`, `feat(db): delivery amount column + cluster lookup`, `feat(scan): tip-only order recovery + amount updates`, `feat(app): weekly delivery spend surfaced`. End every commit body with the Co-Authored-By line given in your dispatch.
