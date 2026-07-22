# Gmail Scan: Include Trash + Follow-Up Filtering

**Date:** 2026-07-22
**Status:** Approved

## Problem

The user's Gmail auto-trashes Uber receipts; the scan's `messages.list` call
excludes trash by default, so every real order is invisible (only inbox noise
reaches the classifier). Additionally each order produces 2–3 emails with the
same subject — order receipt, tip receipt, sometimes a refund adjustment or
cancellation notice — which would over-count a ceiling metric.

## Design

### 1. Include trash/spam in the scan

- `services/gmail_service.fetch_delivery_candidates`: add
  `includeSpamTrash=True` to the `messages().list` call. Rules + AI still
  filter non-orders, so spam inclusion is safe.
- Known limitation (documented, not solved): Gmail purges trash after ~30
  days, so lookback beyond 30 days cannot recover trashed receipts.

### 2. Capture snippets

- Each candidate dict gains a `snippet` field (the Gmail message resource's
  top-level `snippet`, present even with `format="metadata"`; default `""`).

### 3. Follow-up filtering (rule layer)

- New pure function in `receipts.py`: `is_followup(snippet: str) -> bool` —
  case-insensitive match against markers of non-order follow-up emails:
  - `"thanks for tipping"` (tip receipt)
  - `"refunded"` and `"adjusted the total"` (refund adjustments)
  - `"has been canceled"` / `"has been cancelled"` (cancellation notices)
- `jobs/scan_gmail.py`: after the existing `has_delivery_order` skip and
  before rule/AI classification, skip candidates where
  `receipts.is_followup(cand["snippet"])` (no AI call, not stored).
- Known limitation: an order that is later canceled still counts (its
  original receipt was legitimate at scan time). Accepted — rare, and the
  metric is a self-accountability signal, not accounting.

### 4. Snippet-aware AI classification

- `ai_metrics.classify_receipt(sender, subject, snippet="")` gains an
  optional snippet parameter included in the prompt (first 200 chars) —
  same `_call_json` pattern, `MODEL` unchanged. `jobs/scan_gmail.py` passes
  `cand["snippet"]`.

## Testing

- pytest: `is_followup` markers (tip/refund/cancel/plain-order/empty);
  scan-job integration — an order+tip+refund triplet with the same subject
  yields exactly one stored order and zero AI calls for the follow-ups;
  `fetch_delivery_candidates` request includes `includeSpamTrash=True` and
  candidates carry `snippet` (mock the Gmail service per existing test
  patterns); `classify_receipt` prompt contains the snippet when provided.

## Out of Scope

- Retroactive cancellation handling, un-trashing emails, Gmail filter
  changes, cross-email order clustering beyond the follow-up markers.
