def test_format_scorecard_text():
    from jobs.weekly_push import format_scorecard_text
    card = {
        "week_start": "2026-07-13", "week_end": "2026-07-19",
        "metrics": {
            "delivery": {"label": "Delivery orders", "count": 2, "target": 1, "direction": "ceiling", "hit": False},
            "gym": {"label": "Gym sessions", "count": 3, "target": 3, "direction": "floor", "hit": True},
            "social": {"label": "Social events", "count": 2, "target": 2, "direction": "floor", "hit": True},
            "alcohol": {"label": "Alcohol days", "count": 1, "target": 2, "direction": "ceiling", "hit": True},
        },
    }
    text = format_scorecard_text(card)
    assert "2026-07-13" in text
    assert "✅ Gym sessions: 3 (target ≥3)" in text
    assert "❌ Delivery orders: 2 (target ≤1)" in text


def test_push_respects_toggle(temp_db_path, monkeypatch):
    import database as db
    from jobs import weekly_push
    db.seed_default_targets()
    sent = []
    monkeypatch.setattr(weekly_push, "notify", lambda text: sent.append(text) or True)

    weekly_push.run()          # toggle off (default)
    assert sent == []

    db.set_setting("telegram_push", "on")
    weekly_push.run()
    assert len(sent) == 1 and "On Track" in sent[0]
