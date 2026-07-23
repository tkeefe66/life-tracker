"""Scheduled job: scan Gmail for food-delivery receipts (every GMAIL_SCAN_INTERVAL_HOURS)."""
import datetime
import logging

import pytz

import ai_metrics
import database as db
import receipts
from config import TIMEZONE
from services import google_auth
from services.gmail_service import fetch_delivery_candidates

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.datetime.now(pytz.timezone(TIMEZONE)).isoformat()


def run():
    if not google_auth.is_configured():
        logger.warning("Gmail scan skipped: Google not configured")
        db.set_setting("gmail_last_status", "error: Google not configured")
        return
    try:
        candidates = fetch_delivery_candidates()
        ride_examples = db.get_ride_examples()
        added = ai_checked = rides_added = 0
        for cand in candidates:
            if db.has_delivery_order(cand["gmail_message_id"]) or db.has_ride(cand["gmail_message_id"]):
                continue
            snippet = cand.get("snippet", "")
            amount = receipts.extract_amount(snippet)
            day = cand["ordered_at"][:10]

            ride_verdict, ride_service = receipts.classify_ride(cand["sender"], cand["subject"])
            if ride_verdict == "ride":
                key = receipts.extract_ride_time(snippet) or f"{day}|{cand['subject']}|{amount}"
                existing = db.find_ride_by_key(ride_service, key)
                if existing:
                    if amount is not None:
                        db.set_ride_amount(existing["id"], amount)
                    continue
                if db.add_ride(cand["gmail_message_id"], ride_service, cand["ordered_at"],
                               key, cand["subject"], amount):
                    rides_added += 1
                    stored = db.find_ride_by_key(ride_service, key)
                    verdict = ai_metrics.classify_work_ride(
                        ride_service, cand["subject"], snippet, ride_examples)
                    db.set_ride_classification(stored["id"], verdict["is_work"], verdict["confidence"])
                continue

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
        db.set_setting("gmail_last_run", _now_iso())
        db.set_setting("gmail_last_status", "ok")
        db.set_setting(
            "gmail_last_result",
            f"{len(candidates)} candidates · {ai_checked} AI-checked · {added} new orders · "
            f"{rides_added} new rides",
        )
        logger.info(
            "Gmail scan: %d candidates, %d AI-checked, %d new orders, %d new rides",
            len(candidates), ai_checked, added, rides_added,
        )
    except Exception as e:
        logger.exception("Gmail scan failed")
        db.set_setting("gmail_last_run", _now_iso())
        db.set_setting("gmail_last_status", f"error: {e}")
