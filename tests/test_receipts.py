from receipts import classify_candidate, extract_amount, is_followup, is_tip_receipt


def test_uber_eats_order():
    assert classify_candidate("Uber Eats <noreply@uber.com>", "Your order from Illegal Pete's") == ("order", "Uber Eats")


def test_uber_ride_is_not_order():
    assert classify_candidate("Uber Receipts <noreply@uber.com>", "Your Tuesday morning trip with Uber")[0] == "not_order"


def test_doordash_receipt():
    assert classify_candidate("DoorDash <no-reply@doordash.com>", "Order Confirmation for Tom") == ("order", "DoorDash")


def test_promo_is_not_order():
    assert classify_candidate("DoorDash <promo@doordash.com>", "20% off your next order!")[0] == "not_order"
    assert classify_candidate("Grubhub <offers@grubhub.com>", "Free delivery this weekend")[0] == "not_order"


def test_unknown_sender_is_not_order():
    assert classify_candidate("Amazon <ship@amazon.com>", "Your order has shipped") == ("not_order", "")


def test_subdomain_sender_matches():
    assert classify_candidate("<receipts@mail.doordash.com>", "Your receipt")[0] == "order"


def test_known_sender_odd_subject_is_ambiguous():
    verdict, service = classify_candidate("Uber <noreply@uber.com>", "Update on your recent request")
    assert verdict == "ambiguous"
    assert service == "Uber Eats"


def test_is_followup_markers():
    assert is_followup("Tip Jul 20 Thanks for tipping, Tom Here's your receipt")
    assert is_followup("Refunded Just a quick update, Tom")
    assert is_followup("We adjusted the total for your recent order")
    assert is_followup("Your order from Popeyes has been canceled")
    assert is_followup("Your order has been cancelled")
    assert not is_followup("Thanks for ordering, Tom Here's your receipt for Sonic")
    assert not is_followup("")
    assert not is_followup(None)


def test_is_tip_receipt():
    assert is_tip_receipt("Tip Jul 20 Thanks for tipping, Tom Here's your receipt")
    assert not is_tip_receipt("We adjusted the total for your recent order")
    assert not is_tip_receipt("")
    assert not is_tip_receipt(None)


def test_extract_amount():
    assert extract_amount("Subtotal $14.00 Total $16.31") == 16.31
    assert extract_amount("Total $1,024.50") == 1024.5
    assert extract_amount("Total $20") == 20.0
    assert extract_amount("No amount here") is None
    assert extract_amount(None) is None


# ── Rides ─────────────────────────────────────────────────────────────────────

def test_ride_domains_and_classify_ride():
    from receipts import classify_ride
    assert classify_ride("noreply@uber.com", "Your Sunday morning trip with Uber") == ("ride", "Uber")
    assert classify_ride("no-reply@lyft.com", "Your ride with Lyft") == ("ride", "Lyft")
    assert classify_ride("noreply@uber.com", "Your Monday order with Uber Eats")[0] == "not_ride"
    assert classify_ride("noreply@uber.com", "50% off your next ride")[0] == "not_ride"
    assert classify_ride("someone@example.com", "Your trip")[0] == "not_ride"
    assert classify_ride("noreply@uber.com", "Reservation confirmed for Saturday")[0] == "ambiguous"


def test_extract_ride_time():
    from receipts import extract_ride_time
    assert extract_ride_time("Jul 19, 2026 4:03 AM Thanks for riding") == "2026-07-19T04:03"
    assert extract_ride_time("Jul 19, 2026 11:34 PM charge summary") == "2026-07-19T23:34"
    assert extract_ride_time("no timestamp here") is None
    assert extract_ride_time(None) is None


def test_extract_ride_time_lowercase_ampm():
    """Some Uber/Lyft templates render the time marker lowercase."""
    from receipts import extract_ride_time
    assert extract_ride_time("Jul 19, 2026 4:03 am Thanks for riding") == "2026-07-19T04:03"
    assert extract_ride_time("Jul 19, 2026 11:34 pm charge summary") == "2026-07-19T23:34"


def test_extract_ride_time_noon_and_midnight():
    """12-hour math: 12 AM is hour 0, 12 PM is hour 12 — an off-by-twelve bug
    would only surface at the 12:xx boundary."""
    from receipts import extract_ride_time
    assert extract_ride_time("Jan 1, 2026 12:05 AM") == "2026-01-01T00:05"
    assert extract_ride_time("Jan 1, 2026 12:05 PM") == "2026-01-01T12:05"
