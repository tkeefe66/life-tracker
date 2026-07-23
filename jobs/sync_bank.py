"""Scheduled job: sync bank + card transactions from SimpleFIN.

Runs every SIMPLEFIN_SYNC_INTERVAL_HOURS and once at startup. Fetch, upsert,
match pairs, classify — then record a closed-set status. Like every ingestion
job in this repo it must never crash the web app: it logs and records status
rather than raising.

Classification is recomputed from scratch on every run. That is deliberate:
bank_flows is pure and deterministic, so a re-run is free and it means a
newly-arrived transfer half retroactively fixes its partner's flow.
"""
import datetime
import logging

import pytz

import bank_flows
import database as db
from config import INCOME_PAYEE_HINTS, PAIR_WINDOW_DAYS, TIMEZONE
from services import simplefin_service
from services.safe_status import CLOSED_SET, NOT_CONFIGURED, safe_status
from services.simplefin_service import SimpleFinError

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.datetime.now(pytz.timezone(TIMEZONE)).isoformat()


def run(payload=None):
    """Sync from SimpleFIN, or from an already-fetched `payload` (snapshot replay).

    When `payload` is given no network call happens, which is what lets
    scripts/simplefin_backfill.py replay a saved 90-day capture through this
    exact code path instead of a parallel one that could drift.
    """
    if payload is None and not simplefin_service.is_configured():
        logger.warning("Bank sync skipped: SimpleFIN not configured")
        db.set_setting("bank_last_status", NOT_CONFIGURED)
        return
    try:
        if payload is None:
            payload = simplefin_service.fetch_accounts()
        accounts, txns = simplefin_service.normalize(payload)

        now = _now_iso()
        for a in accounts:
            db.upsert_bank_account(a["simplefin_id"], a["name"], a["org"], a["kind"])
            db.touch_bank_account_sync(a["simplefin_id"], now)

        # SimpleFIN ids -> our integer FKs, resolved once.
        stored = db.get_bank_accounts()
        id_by_sfid = {a["simplefin_id"]: a["id"] for a in stored}
        roles_by_id = {a["id"]: a["role"] for a in stored}

        added = skipped = 0
        for t in txns:
            account_id = id_by_sfid.get(t["account_simplefin_id"])
            if account_id is None:
                # No account row means no valid FK. Skip rather than invent one.
                skipped += 1
                continue
            db.upsert_bank_transaction(
                t["simplefin_id"], account_id, t["posted"], t["transacted_at"],
                t["amount"], t["description"], t["payee"], t["memo"], t["mcc"],
            )
            added += 1

        # Re-match and re-classify the ENTIRE table on every run, not a sliding
        # lookback window. A window that slides forward daily would freeze a
        # row's classification the moment it ages out — and the 90-day initial
        # backfill classifies everything while every account role is still
        # "unknown", so an unpaired investment/savings outflow would read
        # "spending" forever. Affordable: single-user app, low thousands of
        # rows, pure/deterministic classification.
        all_txns = db.get_all_bank_transactions()
        # Clear pair_id before handing rows to the matcher so pairing is
        # recomputed from scratch too — a pending amount that later settles to
        # a different value must be free to lose its old pairing rather than
        # keep a stale (and now wrong) pair_id/card_payment flow forever.
        fresh = [{**t, "pair_id": None} for t in all_txns]
        pair_map = bank_flows.match_pairs(fresh, window_days=PAIR_WINDOW_DAYS)
        derived = bank_flows.classify_all(fresh, roles_by_id, pair_map, INCOME_PAYEE_HINTS)

        # Written in ONE database transaction (set_bank_transactions_derived_bulk),
        # not one commit per row. A per-row write left a hole: an interrupted run
        # could leave one half of a matched pair with its pair_id set and the
        # other half free, and the free half would not self-heal — it could
        # mis-pair with something else on the next sync. All-or-nothing closes
        # that hole.
        db.set_bank_transactions_derived_bulk(
            (sfid, flow, pair_id, ambiguous)
            for sfid, (flow, pair_id, ambiguous) in derived.items()
        )

        counts = {}
        for flow, _, _ in derived.values():
            counts[flow] = counts.get(flow, 0) + 1
        unknown_roles = sum(1 for a in stored if a["role"] == "unknown")

        db.set_setting("bank_last_run", now)
        db.set_setting("bank_last_status", "ok")
        db.set_setting(
            "bank_last_result",
            f"{len(accounts)} accounts · {added} transactions · {skipped} skipped · "
            f"{counts.get('spending', 0)} spending · {counts.get('transfer', 0)} transfers · "
            f"{counts.get('card_payment', 0)} card payments · "
            f"{counts.get('inflow_unknown', 0)} unknown inflows · "
            f"{unknown_roles} accounts need a role",
        )
        logger.info("Bank sync: %d accounts, %d transactions, %d skipped, flows=%s",
                    len(accounts), added, skipped, counts)
    except SimpleFinError as e:
        # Already logged server-side inside the service. `e.status` carries no
        # message text, but `SimpleFinError.__init__` accepts any string — the
        # closed-set invariant is only convention there, so re-check it here
        # rather than trust it by construction.
        db.set_setting("bank_last_run", _now_iso())
        db.set_setting("bank_last_status", e.status if e.status in CLOSED_SET else "error: see logs")
    except Exception as e:
        # Full detail server-side only. The DB value must come from the closed
        # set — never str(e) — because the SimpleFIN URL carries the user's bank
        # credentials inside the URL itself.
        logger.exception("Bank sync failed")
        db.set_setting("bank_last_run", _now_iso())
        db.set_setting("bank_last_status", safe_status(e))
