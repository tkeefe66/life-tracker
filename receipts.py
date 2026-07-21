"""Rule-based food-delivery receipt detection. Ambiguous cases go to ai_metrics."""
import re

DELIVERY_DOMAINS = {
    "uber.com": "Uber Eats",
    "ubereats.com": "Uber Eats",
    "doordash.com": "DoorDash",
    "grubhub.com": "Grubhub",
    "seamless.com": "Seamless",
    "postmates.com": "Postmates",
    "trycaviar.com": "Caviar",
}

_ORDER_RE = re.compile(r"\b(order|receipt|delivered|delivery confirmation)\b", re.IGNORECASE)
_RIDE_RE = re.compile(r"\b(trip|ride|driver)\b", re.IGNORECASE)
_PROMO_RE = re.compile(r"(\d+% off|promo|deal|free delivery|save \$|don't miss|invite)", re.IGNORECASE)


def _sender_domain(sender: str) -> str:
    m = re.search(r"@([\w.-]+)", sender)
    if not m:
        return ""
    domain = m.group(1).lower().rstrip(">")
    for known in DELIVERY_DOMAINS:
        if domain == known or domain.endswith("." + known):
            return known
    return ""


def classify_candidate(sender: str, subject: str) -> tuple:
    domain = _sender_domain(sender)
    if not domain:
        return "not_order", ""
    service = DELIVERY_DOMAINS[domain]
    if _RIDE_RE.search(subject):
        return "not_order", service
    if _PROMO_RE.search(subject):
        return "not_order", service
    if _ORDER_RE.search(subject):
        return "order", service
    return "ambiguous", service
