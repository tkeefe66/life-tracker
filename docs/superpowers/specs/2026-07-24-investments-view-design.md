# Investments View — live holdings, never stored

**Date:** 2026-07-24
**Status:** Approved in conversation

The Fidelity accounts hold ~34 positions across five accounts (ROTH IRA,
Individual TOD, Traditional/Rollover IRA, 401k). SimpleFIN's payload carries a
`holdings` array per account — `symbol`, `description`, `shares`, `cost_basis`,
`purchase_price`, `market_value` — which the sync currently ignores. The user
wants to see what the investments are and how they're doing ($/% up or down).

## Decisions

1. **Live-only, never persisted.** The bank spec's deliberate stance —
   "balances are never stored" — extends to holdings: market values in a
   table would reconstruct portfolio value and flow into Postgres and the
   weekly S3 backups. Opening the section fetches from SimpleFIN, computes,
   displays, discards. Nothing lands in the DB, `app_settings`, or logs.
2. **Gain is vs. cost basis only.** `market_value − cost_basis` per holding,
   `%` guarded against zero/absent basis (show value with no gain figure
   rather than a division error). SimpleFIN has no price history, so
   day/week change is impossible without storage — explicitly out of scope.
   Caveat accepted in conversation: cost basis is whatever Fidelity reports
   (contributions for a 401k, reinvestment-adjusted for funds), unmassaged.
3. **Fetch on expand, not on screen load.** A SimpleFIN round-trip takes
   seconds; the Money screen stays fast when the user is only checking
   spending. The section fetches when opened, with a loading state.
4. **Layout: total + per-account groups.** Portfolio total with overall $/%
   vs cost at top, then one group per account (resolved nickname), holdings
   sorted by descending market value: symbol, description, value, signed
   gain $ and %.
5. **The fetch is transactions-light.** Reuse
   `simplefin_service.fetch_accounts(days=1)` — holdings come regardless of
   the window; a 1-day window keeps the payload small. Same redaction
   boundary: only `SimpleFinError` with a closed-set status ever crosses.
6. **No AI, by construction.** Holdings are not in `METRICS`, so the
   reflection and Telegram paths never see them — same argument that keeps
   bank data out of those prompts today. No `ai_metrics` involvement at all.
7. **Accounts with zero holdings are omitted** from the response (every
   checking/credit account has an empty `holdings` array).

## API

- `GET /api/bank/investments` (protected) →
  ```json
  {
    "total": {"market_value": N, "cost_basis": N, "gain": N, "gain_pct": N},
    "accounts": [{
      "simplefin_id": "…", "name": "<resolved display name>",
      "market_value": N, "cost_basis": N, "gain": N, "gain_pct": N,
      "holdings": [{"symbol": "VOO", "description": "…", "shares": N,
                    "market_value": N, "cost_basis": N,
                    "gain": N, "gain_pct": N}, …]
    }, …]
  }
  ```
  `gain`/`gain_pct` are null when cost basis is missing/zero; null-basis
  holdings still count toward `market_value` totals but are excluded from
  total `cost_basis`/`gain_pct`. 503 with the closed-set status string on
  `SimpleFinError`; 200 with `{"accounts": []}` when SimpleFIN is not
  configured.

## Components

- `services/simplefin_service.normalize_holdings(payload)` — companion to
  `normalize()`: per-account holdings with the same defensive coercion
  (floats via try/except, non-finite dropped, absent fields tolerated).
  Balances still dropped.
- `app/money.py:investments()` — fetches, normalizes, computes gains and
  rounds once at the end, resolves account display names against
  `db.get_bank_accounts()` (nickname resolution from the companion spec).
- `app/routes.py` — thin route; catches `SimpleFinError`, returns status.
- `frontend` — new `Investments.tsx` component on the Money screen,
  collapsed header ("Investments"), fetch-on-expand, quiet inline error
  line on failure (secondary surface: never the screen-level error).
  Gain colored with existing tokens (green up / red down), value + signed
  gain via `money`/`signedMoney`, new pure `pct()` helper in `lib.ts`.

## Testing

pytest: `normalize_holdings` (shape, coercion, malformed payloads);
`investments()` math incl. zero-basis and rounding (service monkeypatched —
no network); route test for the not-configured and error paths. vitest:
`pct()` formatting. `tsc --noEmit` + `vite build` + manual look.
