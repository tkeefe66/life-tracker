"""Pure metric computation — no DB, no I/O."""

METRICS = {
    "delivery": {"label": "Delivery orders", "direction": "ceiling", "default_target": 1},
    "gym": {"label": "Gym sessions", "direction": "floor", "default_target": 3},
    "social": {"label": "Social events", "direction": "floor", "default_target": 2},
    "alcohol": {"label": "Alcohol days", "direction": "ceiling", "default_target": 2},
}
