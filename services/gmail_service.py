"""Gmail service — finds food-delivery receipt candidates (gmail.readonly)."""
import datetime
import logging

import pytz

from config import GMAIL_SCAN_LOOKBACK_DAYS, TIMEZONE
from receipts import DELIVERY_DOMAINS
from services import google_auth

logger = logging.getLogger(__name__)

_SENDERS = "from:(" + " OR ".join(DELIVERY_DOMAINS) + ")"


def _query() -> str:
    return f"{_SENDERS} newer_than:{GMAIL_SCAN_LOOKBACK_DAYS}d"


def _get_service():
    return google_auth.build_service("gmail", "v1")


def fetch_delivery_candidates() -> list:
    """Messages from known delivery senders in the lookback window (GMAIL_SCAN_LOOKBACK_DAYS).
    Returns dicts: gmail_message_id, sender, subject, ordered_at (local-tz ISO)."""
    service = _get_service()
    tz = pytz.timezone(TIMEZONE)
    resp = service.users().messages().list(userId="me", q=_query(), maxResults=100).execute()
    out = []
    for ref in resp.get("messages", []) or []:
        msg = service.users().messages().get(
            userId="me", id=ref["id"], format="metadata", metadataHeaders=["From", "Subject"]
        ).execute()
        headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
        ts = int(msg.get("internalDate", "0")) / 1000
        ordered_at = datetime.datetime.fromtimestamp(ts, tz).isoformat()
        out.append({
            "gmail_message_id": ref["id"],
            "sender": headers.get("from", ""),
            "subject": headers.get("subject", ""),
            "ordered_at": ordered_at,
        })
    logger.info("Gmail: %d delivery-sender candidates", len(out))
    return out
