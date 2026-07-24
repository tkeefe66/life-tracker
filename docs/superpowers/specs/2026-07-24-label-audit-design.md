# Label Audit — "Needs a look" + suggested badges

**Date:** 2026-07-24
**Status:** Approved in conversation (mockup comparison; user chose "A with B
badge")

Auto-applied label suggestions need a review surface: wrong inheritance must
not hide inside drill-downs the user never opens. Two additions, chosen from
side-by-side mockups:

1. **"Needs a look" audit section** (option A) — a card list of every
   unconfirmed suggestion on the Money screen, one tap to confirm / change /
   reject. Hidden when empty.
2. **Suggested-count badges** (option B's badge, folded in) — label-view
   lines show how many of their rows are inherited-but-unconfirmed.

## Decisions

1. **Rejection must survive the sync.** The suggestion pass recomputes the
   whole table every run, so "No label" cannot just clear `suggested_label` —
   it would come back next sync. A new USER column `user_no_label`
   (boolean, default false) records the verdict: this row gets no label.
   The sync never touches it (Override + Learning rule 3), the suggestion
   pass returns None for flagged rows, and resolution respects it in SQL.
2. **`user_label` and `user_no_label` are mutually exclusive**, enforced by
   the route: setting a real label clears the flag; setting the flag clears
   the label. Both are user verdicts on the same question.
3. **Resolution stays in SQL, one place:** `resolved_label` becomes
   `CASE WHEN user_no_label THEN NULL ELSE COALESCE(user_label,
   suggested_label) END`. A rejected row is Unlabeled everywhere instantly —
   no waiting for the next sync to retire the lingering `suggested_label`.
4. **Bulk apply and sibling counts skip rejected rows.** "Apply to N more"
   respects a row's explicit no-label verdict; `user_no_label` rows are
   excluded from `set_bank_labels_by_vendor`, `count_bank_unlabeled_by_vendor`,
   and the suggestion pass.
5. **The audit list is capped and counted** like triage: `limit` clamped
   1–200, plus a table-wide total so the UI can say "N more".
6. **Placement:** below the triage queues ("Needs a decision" area), styled
   as cards (`--surface-2`, `--r`), section label "Suggested labels — needs
   a look". Secondary surface: failed fetch hides it.

## API

- `GET /api/bank/label-suggestions?limit=50` → `{"rows": [{simplefin_id,
  posted, amount, vendor, account_name, suggested_label, description}, …],
  "total": N}` — rows where `suggested_label IS NOT NULL AND user_label IS
  NULL AND NOT user_no_label`, newest first. Assembled by
  `app/money.py:label_suggestions(limit)`; SQL in `database.py`.
- `POST /api/bank/label` gains a third single-row form:
  `{"simplefin_id": …, "no_label": true}` → sets `user_no_label`, clears
  `user_label`; response `{"ok": true, "label": null, "siblings": 0,
  "vendor": …}`. Sending a real label clears `user_no_label`. `no_label`
  combined with `payee` (bulk) or with a non-empty `label` → 400.
- `GET /api/bank/breakdown?by=label` lines gain `"suggested": N` — the count
  of contributing spending-side rows in that line whose label came from a
  suggestion (`user_label IS NULL`). 0 for the Unlabeled line and for
  vendor-mode lines (field present only in label mode).

## Frontend

- **Audit section** on the Money screen after the triage queues: count line
  ("N transactions inherited a label you haven't confirmed"), then cards —
  vendor + amount, meta line (date · account · suggested: *label*), buttons
  `✓ <label>` (primary; confirms via existing label POST), `Change…` (opens
  the same inline editor used in drill-downs, prefilled), `No label`
  (no_label POST). Optimistic removal; failed POST restores the card.
  "N more" footer when total exceeds the fetched page. Section renders
  nothing when the list is empty.
- **Badges**: label-view lines with `suggested > 0` render a
  `N suggested` pill (accent-soft background) after the label name.
- Confirming/rejecting refreshes the audit list locally and invalidates the
  breakdown the same way the existing label editor does (no full-screen
  refetch).

## Out of scope

- No new AI, no schema beyond the one boolean, no changes to the weekly
  push / reflection boundary (labels still never leave the Money screen's
  API surface).
