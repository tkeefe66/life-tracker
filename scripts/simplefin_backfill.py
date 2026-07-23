"""One-off: replay a saved SimpleFIN snapshot through the normal ingest path.

SimpleFIN keeps a rolling 90 days, so snapshots taken by
scripts/simplefin_snapshot.py are the only copy of anything older than the live
window. This feeds them to jobs.sync_bank.run(payload=...) — the same code path
a live sync uses, so backfilled rows are indistinguishable from synced ones and
the ingest logic can never drift between the two.

Safe to re-run: every upsert keys on simplefin_id, and classification is
recomputed deterministically.

Usage:
    python scripts/simplefin_backfill.py                      # newest snapshot
    python scripts/simplefin_backfill.py --all                # every snapshot, oldest first
    python scripts/simplefin_backfill.py path/to/snapshot.json
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SNAPSHOT_DIR = Path.home() / ".on-track" / "simplefin-snapshots"


def _load(path: Path) -> dict:
    envelope = json.loads(path.read_text())
    # Snapshots are wrapped in a capture envelope; older ad-hoc dumps may not be.
    return envelope.get("payload", envelope)


# The account roles the user gave, from the spec's "Account roles" table. Matched
# as case-insensitive substrings against "<org> <name>", first match wins.
#
# This seeds the INITIAL load only: seed_roles never touches an account whose
# role is already set, so "a new account is surfaced, never silently guessed"
# stays true for everything that arrives later. Roles remain data, not code —
# editable via the API without a deploy.
ROLE_SEEDS = [
    ("wells fargo", "7395", "spending"),   # primary day-to-day, pays the Amex
    ("wells fargo", "4116", "bills"),
    ("wells fargo", "0407", "savings"),    # savings_dynamic — in and out by design
    ("american express", "", "credit_card"),
    ("chase", "", "credit_card"),
    ("barclays", "", "credit_card"),
    ("citi", "", "credit_card"),
    ("fidelity", "", "investment"),        # covers all five: 401k, Roth, Trad, Rollover, Individual
]


def seed_roles(db) -> int:
    """Apply ROLE_SEEDS to accounts still marked `unknown`. Returns how many changed."""
    changed = 0
    for acct in db.get_bank_accounts():
        if acct["role"] != "unknown":
            continue
        haystack = f"{acct['org']} {acct['name']}".lower()
        for org_hint, id_hint, role in ROLE_SEEDS:
            if org_hint in haystack and (not id_hint or id_hint in haystack):
                db.set_bank_account_role(acct["simplefin_id"], role)
                print(f"  role: {acct['name'][:34]:36} -> {role}")
                changed += 1
                break
    return changed


def main() -> int:
    args = [a for a in sys.argv[1:]]
    if "--all" in args:
        args.remove("--all")
        paths = sorted(SNAPSHOT_DIR.glob("simplefin-*.json"))
    elif args:
        paths = [Path(args.pop(0)).expanduser()]
    else:
        paths = sorted(SNAPSHOT_DIR.glob("simplefin-*.json"))[-1:]

    if not paths:
        print(f"No snapshots found in {SNAPSHOT_DIR}.", file=sys.stderr)
        print("Run scripts/simplefin_snapshot.py first.", file=sys.stderr)
        return 1

    import database as db
    from jobs.sync_bank import run as sync_bank

    db.initialize_db()

    for path in paths:
        if not path.exists():
            print(f"Missing: {path}", file=sys.stderr)
            return 1
        print(f"Replaying {path.name} …")
        sync_bank(payload=_load(path))
        print(f"  {db.get_setting('bank_last_result')}")

    seeded = seed_roles(db)
    if seeded:
        # Roles drive pair matching, so the first pass classified against
        # `unknown` everywhere. Re-run to let card payments and transfers resolve.
        print(f"\nSeeded {seeded} account role(s); re-classifying …")
        for path in paths:
            sync_bank(payload=_load(path))
        print(f"  {db.get_setting('bank_last_result')}")

    unknown = [a for a in db.get_bank_accounts() if a["role"] == "unknown"]
    if unknown:
        print(f"\n{len(unknown)} account(s) still need a role — classification "
              f"treats them as unknown, so their transfers will not pair correctly:")
        for a in unknown:
            print(f"  {a['simplefin_id']:24} {a['name'][:32]:34} ({a['org']})")
        print("\nSet each with: POST /api/bank/accounts/<simplefin_id>/role "
              '{"role": "spending|bills|savings|investment|credit_card"}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
