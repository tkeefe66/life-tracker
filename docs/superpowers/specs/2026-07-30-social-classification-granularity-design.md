# Social Classification Granularity

**Date:** 2026-07-30
**Status:** Implemented

## Incident

The just-shipped "Didn't happen" button (`frontend/src/screens/Today.tsx`,
`PATCH /api/social/{id}` with `{is_social: false}`) wrote `user_is_social =
false` — the same column that means "this event TYPE isn't social."
`db.get_classification_examples()` feeds every `user_is_social` override back
to `ai_metrics.classify_social_event` as a few-shot example, with no
distinction between "this occurrence didn't happen" and "this kind of event
isn't social." One canceled kickball game, marked "Didn't happen," taught the
model that kickball in general isn't social — and it went on to
misclassify three real, later kickball events as not-social.

The root problem was two distinct facts sharing one column, and a one-off
answer being treated as if it generalized. Both had to be fixed for the
override + learning pattern to be trustworthy again.

## Occurrence vs. Type

The fix is a new nullable boolean, `user_removed`, on `calendar_events`,
strictly separate from `user_is_social`:

- `user_is_social` answers "is THIS KIND of event social?" — a classification
  correction, fed back to the model as an example.
- `user_removed` answers "did THIS OCCURRENCE happen?" — a fact about one
  calendar entry, never fed to the model, never touching `user_is_social`.

The "Didn't happen" / Undo buttons on Today now PATCH `{removed: true}` /
`{removed: false}`. The edit form's "Counts as social" checkbox is unchanged
— it still writes `is_social`, because that one genuinely is a
classification correction. `get_events_for_day` and `get_social_events_range`
both now exclude `user_removed IS NOT TRUE` rows, so a removed event
disappears from every social surface (counts, day view) the same way it did
before, but without leaving behind a bogus "not social" verdict.

A one-time migration converts the one row in production affected by the old
conflation — the canceled 2026-07-22 Volo kickball event, which had
`source='gcal' AND user_is_social=false` — to `user_removed=true,
user_is_social=NULL`. The conversion runs exactly once, gated on the column
being newly added (mirrors the SQLite/Postgres migration-guard pattern
already used elsewhere in `_init_v2_tables()`), so it can never re-fire and
reinterpret a genuine future "not social" correction as a removal.

## Series-Only Generalization

Splitting the column stops new poisoning, but it doesn't retroactively make
a one-off correction into a trustworthy generalization. A single
"kickball-was-canceled-so-not-social" answer was never good evidence about
kickball as a category — it was evidence about one Tuesday.

`recurring_event_id` (Google's own `recurringEventId`, passed through
unchanged by `services/calendar_service.py` and stored by
`jobs/scan_calendar.py`) now gates `get_classification_examples()`: only rows
with a **non-null `user_is_social` AND a non-null `recurring_event_id`**
qualify. Recurring-series membership is the actual signal that a verdict
generalizes — "this Tuesday kickball is social" says something meaningful
about "Tuesday kickball" as a series; a one-off dentist appointment marked
not-social says nothing about dentist appointments in general (there's
nothing to generalize to). One-off events never contribute an example,
full stop.

**Conflict exclusion.** If two occurrences of the same series carry
contradictory user verdicts (one confirmed social, another confirmed not),
the whole series is excluded from the examples set rather than picking a
side. Split opinion inside one series is not a signal to hand the model as
ground truth — it means the series itself is ambiguous, which is exactly
the kind of case the model should reason about fresh rather than be told a
confident (and wrong, for half the cases) answer.

An agreeing series contributes exactly one example — its most recent
verdict — so a long-running weekly series doesn't crowd out everything
else in the (still ~10-capped) few-shot list.

## Ask When Unsure

Complementing the model in gathering better signal: the classification
prompt was widened to name the actual hard case — inherently either-way
activities (a movie, a restaurant meal, a hobby that's sometimes solo,
sometimes group) where title/description/attendees genuinely don't decide
the question. The instruction is explicit: pick a best-effort lean, but
return a **low confidence score** rather than a confident guess when the
signal can't support one. The response shape (`is_social` + `confidence`)
is unchanged — only the guidance around when confidence should be low.

That confidence score now does something new: a single named constant,
`metrics.AMBIGUOUS_CONFIDENCE = 0.7`, marks the line below which an
unresolved event (`user_is_social IS NULL`, not `user_removed`) is surfaced
as `uncertain: true` in the day payload — regardless of which way the AI's
lean points. Previously `get_events_for_day` only ever returned
resolved-social rows; now a low-confidence NOT-social lean also appears
(with the ambiguity marker), because the point is to ask the user, not to
silently drop a genuinely uncertain event from the log. Confident or
already-resolved rows always carry `uncertain: false`.

On Today, an `uncertain` row keeps its normal display and gains a quiet
inline chip — "social? **Yes** / **No**" — beneath it. One tap PATCHes
`{is_social: true|false}` (`buildUncertainResolvePatch` in `lib.ts`); the
row re-renders as a normal (or absent, if answered No) row on the next
refresh, with the chip gone because `user_is_social` is no longer null.

### Follows-the-Lean (Decision)

**Counting is unchanged while a chip is unanswered.** The scorecard/week
counts keep using resolved `COALESCE(user_is_social, is_social) = true` —
exactly as before this feature. An uncertain event that leans social still
counts as social until the user says otherwise; an uncertain event that
leans not-social still doesn't count, but is now visible (with the chip)
rather than invisible.

**Rejected: doesn't-count-until-answered.** The alternative — hold an
uncertain event out of every count until the user explicitly resolves it —
was considered and rejected. This is a pull-based, single-user app: nothing
pushes a notification demanding an answer, and a user who doesn't open Today
for several days would see their weekly score silently miss real (or
silently include already-passed) events with no visible sign anything was
being withheld. A silently-degraded scorecard is worse than a
score that occasionally leans on an unconfirmed but reasonable AI guess —
especially since the chip makes the assumption visible the moment the user
does look. Follows-the-lean keeps the score always fully computed and
makes the uncertainty an explicit, correctable annotation rather than a
hidden gap.

## Future Work (recorded verbatim, not yet implemented)

- Bank-transaction-to-event association (e.g. "Adi Bday Downtown" on
  2026-07-24 with many card charges) + spend-amount as a solo-vs-group
  signal.
- That feature will widen the AI privacy boundary — bank payees currently
  reach Claude ONLY via `suggest_bank_flows` (see CLAUDE.md's "Bank
  payee/description may reach Claude in exactly one place" rule) — so
  feeding spend into the social classifier must be a deliberate decision
  then, not a side effect.

## Rejected Alternatives

- **Fixing `get_classification_examples` alone, without splitting the
  column.** Filtering examples more carefully doesn't stop the underlying
  conflation — the very next "Didn't happen" tap would still write
  `user_is_social = false`, still be readable as a real classification
  answer by anything else that queries the column (or a future feature),
  and still require the user to re-litigate a canceled event through the
  "Counts as social" checkbox instead of a dedicated undo-able action. The
  column split is the actual fix; the examples-query change is what makes
  the split's benefit reach the model.
- **A confidence floor instead of series-gating for examples.** Using
  `ai_confidence` (the model's OWN uncertainty at classification time) to
  decide which user corrections to trust as examples was considered, but
  it answers a different question: the model's confidence about the
  original guess says nothing about whether the user's correction
  generalizes to other events. A confident wrong guess on a one-off event,
  corrected once, is exactly the poisoning scenario — series membership is
  the property that actually matters.
- **Counting only confidently-resolved events (doesn't-count-until-
  answered).** See "Follows-the-Lean" above.
