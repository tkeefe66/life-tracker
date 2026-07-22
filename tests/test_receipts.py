from receipts import classify_candidate, is_followup


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
