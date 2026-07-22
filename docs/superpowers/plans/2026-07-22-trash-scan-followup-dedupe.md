# Trash-Inclusive Scan + Follow-Up Filtering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Gmail scan sees trashed receipts and counts each order exactly once by skipping tip/refund/cancellation follow-up emails.

**Architecture:** `includeSpamTrash=True` + snippet capture in `gmail_service`; a pure `is_followup(snippet)` rule in `receipts.py` applied in the scan job before any AI call; `classify_receipt` gains an optional snippet for better ambiguous-case decisions.

**Tech Stack:** Python/FastAPI backend only. No frontend changes, no schema changes.

**Spec:** `docs/superpowers/specs/2026-07-22-trash-scan-followup-dedupe-design.md`

## Global Constraints

- `ai_metrics.py` only Claude caller, `_call_json` pattern, `MODEL` unchanged.
- Follow-up markers (case-insensitive): "thanks for tipping", "refunded", "adjusted the total", "has been canceled"/"has been cancelled". Follow-ups are skipped with NO AI call and are not stored.
- Follow-up skip happens after the `has_delivery_order` dedupe skip and before rule/AI classification.
- The scan job must use `cand.get("snippet", "")` (never `cand["snippet"]`) so candidates without the key can't crash the job.
- Backend tests: `pytest tests/ -v`. No commits with failing tests.

---

### Task 1: Trash inclusion, snippet capture, follow-up rules

**Files:**
- Modify: `services/gmail_service.py`
- Modify: `receipts.py`
- Modify: `jobs/scan_gmail.py`
- Modify: `ai_metrics.py`
- Test: `tests/test_receipts.py`, `tests/test_scan_gmail.py`, `tests/test_ai_metrics.py`

**Interfaces:**
- Produces: candidate dicts gain `snippet: str`; `receipts.is_followup(snippet) -> bool`; `ai_metrics.classify_receipt(sender, subject, snippet="") -> bool` (backward-compatible default).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_receipts.py`:

```python
from receipts import is_followup


def test_is_followup_markers():
    assert is_followup("Tip Jul 20 Thanks for tipping, Tom Here's your receipt")
    assert is_followup("Refunded Just a quick update, Tom")
    assert is_followup("We adjusted the total for your recent order")
    assert is_followup("Your order from Popeyes has been canceled")
    assert is_followup("Your order has been cancelled")
    assert not is_followup("Thanks for ordering, Tom Here's your receipt for Sonic")
    assert not is_followup("")
    assert not is_followup(None)
```

Append to `tests/test_scan_gmail.py`:

```python
def test_scan_skips_followup_emails(temp_db_path, monkeypatch):
    import database as db
    from jobs import scan_gmail

    triplet = [
        {"gmail_message_id": "o1", "sender": "noreply@uber.com",
         "subject": "Your Monday evening order with Uber Eats",
         "ordered_at": "2026-07-15T21:01:00-06:00",
         "snippet": "Thanks for ordering, Tom Here's your receipt for Oblio's"},
        {"gmail_message_id": "o2", "sender": "noreply@uber.com",
         "subject": "Your Monday evening order with Uber Eats",
         "ordered_at": "2026-07-15T22:01:00-06:00",
         "snippet": "Tip Thanks for tipping, Tom Here's your receipt for Oblio's"},
        {"gmail_message_id": "o3", "sender": "noreply@uber.com",
         "subject": "Your Monday evening order with Uber Eats",
         "ordered_at": "2026-07-15T22:30:00-06:00",
         "snippet": "Refunded Just a quick update, Tom We adjusted the total"},
    ]
    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", lambda: triplet)
    monkeypatch.setattr(scan_gmail.google_auth, "is_configured", lambda: True)
    ai_calls = []
    monkeypatch.setattr(scan_gmail.ai_metrics, "classify_receipt",
                        lambda s, subj, snip="": ai_calls.append(subj) or True)

    scan_gmail.run()
    stored = db.get_delivery_orders_range("2026-07-14", "2026-07-16")
    assert [r["gmail_message_id"] for r in stored] == ["o1"]
    assert ai_calls == []  # follow-ups skipped by rules; o1's subject rule-matches "order"


def test_fetch_includes_trash_and_snippet(monkeypatch):
    from services import gmail_service

    captured = {}

    class FakeReq:
        def __init__(self, result):
            self._r = result

        def execute(self):
            return self._r

    class FakeMessages:
        def list(self, **kw):
            captured.update(kw)
            return FakeReq({"messages": [{"id": "x1"}]})

        def get(self, **kw):
            return FakeReq({
                "payload": {"headers": [
                    {"name": "From", "value": "noreply@uber.com"},
                    {"name": "Subject", "value": "Your order"},
                ]},
                "internalDate": "1753200000000",
                "snippet": "Thanks for ordering",
            })

    class FakeUsers:
        def messages(self):
            return FakeMessages()

    class FakeService:
        def users(self):
            return FakeUsers()

    monkeypatch.setattr(gmail_service, "_get_service", lambda: FakeService())
    out = gmail_service.fetch_delivery_candidates()
    assert captured["includeSpamTrash"] is True
    assert out[0]["snippet"] == "Thanks for ordering"
```

Also in `tests/test_scan_gmail.py`, update the TWO existing `classify_receipt` mock lambdas (in `test_scan_stores_orders_and_uses_ai_for_ambiguous` and `test_scan_skips_already_seen`) from `lambda s, subj: ...` to `lambda s, subj, snip="": ...` — the job will now pass a third argument.

Append to `tests/test_ai_metrics.py` (adapt the canned-response call to the file's `_set_response` helper):

```python
def test_classify_receipt_includes_snippet(mock_anthropic):
    _set_response(mock_anthropic, '{"is_order": true}')
    import ai_metrics
    assert ai_metrics.classify_receipt(
        "noreply@uber.com", "Your order", "Thanks for ordering, preview text"
    ) is True
    prompt = mock_anthropic.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "Thanks for ordering, preview text" in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_receipts.py tests/test_scan_gmail.py tests/test_ai_metrics.py -v -k "followup or trash or snippet"`
Expected: FAIL — `is_followup` missing, no `includeSpamTrash`/`snippet`, prompt lacks snippet.

- [ ] **Step 3: Implement**

`receipts.py` — after `_PROMO_RE`:

```python
_FOLLOWUP_RE = re.compile(
    r"(thanks for tipping|refunded|adjusted the total|has been cancell?ed)",
    re.IGNORECASE,
)


def is_followup(snippet) -> bool:
    """True for follow-up emails about an existing order (tip receipt,
    refund adjustment, cancellation notice) — not a new order."""
    return bool(_FOLLOWUP_RE.search(snippet or ""))
```

`services/gmail_service.py` — in `fetch_delivery_candidates`:

```python
    resp = service.users().messages().list(
        userId="me", q=_query(), maxResults=100, includeSpamTrash=True
    ).execute()
```

and in the candidate dict:

```python
            "snippet": msg.get("snippet", ""),
```

Update the docstring to mention trash inclusion.

`jobs/scan_gmail.py` — in the candidate loop:

```python
        for cand in candidates:
            if db.has_delivery_order(cand["gmail_message_id"]):
                continue
            if receipts.is_followup(cand.get("snippet", "")):
                continue
            verdict, service = receipts.classify_candidate(cand["sender"], cand["subject"])
            if verdict == "ambiguous":
                ai_checked += 1
                verdict = "order" if ai_metrics.classify_receipt(
                    cand["sender"], cand["subject"], cand.get("snippet", "")
                ) else "not_order"
```

`ai_metrics.py` — `classify_receipt` gains the parameter and prompt lines:

```python
def classify_receipt(sender: str, subject: str, snippet: str = "") -> bool:
    """True if this email is a receipt/confirmation for a FOOD DELIVERY ORDER
    (not a ride, promo, refund notice, tip receipt, or account email)."""
    prompt = f"""You classify emails. Is this email a receipt or confirmation for a food
delivery ORDER the user placed (Uber Eats, DoorDash, Grubhub, etc.)?

Promotions, ride receipts, refunds, password resets, and newsletters are NOT orders.
Tip receipts, refund adjustments, and cancellation notices for an EXISTING order
are NOT new orders.

From: {sender}
Subject: {subject}
Preview: {snippet[:200]}

Reply with only JSON: {{"is_order": true|false}}"""
    result = _call_json(prompt, default={"is_order": False})
    return bool(result.get("is_order", False))
```

- [ ] **Step 4: Run the full backend suite**

Run: `pytest tests/ -v`
Expected: all PASS (including the two updated lambdas).

- [ ] **Step 5: Commit**

```bash
git add services/gmail_service.py receipts.py jobs/scan_gmail.py ai_metrics.py tests/test_receipts.py tests/test_scan_gmail.py tests/test_ai_metrics.py
git commit -m "feat(gmail): scan trash for receipts, skip tip/refund/cancel follow-ups"
```

---

### Task 2 (ops, run by controller)

- [ ] Merge to main, push (auto-deploys; startup scan re-runs with 30-day window).
- [ ] Verify logs: `Gmail scan: N candidates, ...` with N ≈ 25–30 (trash included) and roughly 14 new orders stored.
- [ ] Spot-check `/api/deliveries` count and the Detected-orders list in Settings.
