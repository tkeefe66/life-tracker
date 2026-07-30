# Social Event "Didn't Happen" — Affordance + Undo

**Date:** 2026-07-29
**Status:** Approved in conversation; frontend-only (the existing
`PATCH /api/social/{id}` route already does everything the backend needs)

## Problem

Calendar-detected social events render under "Noticed quietly" on the Day
screen as `<button className="quiet quiet-btn">` rows. Two problems:

1. **No affordance.** The row looks like static text — plain text, no
   chevron, no hover/press feedback, no cursor change — so nothing signals
   it's tappable.
2. **Removing a canceled event is buried and irreversible.** Marking an
   event "not social" (the only way to make an uncountable, canceled event
   stop counting) requires tap → uncheck "Counts as social" → Save, a flow
   built for correcting a misclassification, not for saying "this didn't
   happen." And once removed, the event vanishes from the `/today` response
   entirely (`get_events_for_day` filters to
   `COALESCE(user_is_social, is_social) = true`) — there is no way to see it
   was there, let alone put it back, without leaving the screen.

## Chosen Design

1. **Visible affordance.** `button.quiet-btn` (shared by delivery-adjacent
   ride rows and social rows) gets a trailing chevron (`›`, via `::after`,
   colored `--muted`) plus `cursor: pointer` and a hover/active
   `--accent-soft` background — the same token the row already used for
   `:active`, now also on `:hover` and paired with a transition. Plain
   `.quiet` rows (deliveries) are untouched; they were never tappable.
2. **Explicit "Didn't happen" action.** In the edit form, detected events
   (`e.source !== "manual"`) get a danger-styled **"Didn't happen"** button
   in the same slot manual events use for **Delete** — same visual weight,
   same `row-actions` / `.danger` styling, different verb because a detected
   event isn't deleted, it's marked not-social
   (`PATCH { is_social: false }`, i.e. `user_is_social = false`).
3. **Local-state undo.** The removed event does not disappear from the list.
   `Today.tsx` keeps a `removed: Record<gcal_event_id, SocialEvent>` map
   populated on "Didn't happen" and merged back into the fetched list by a
   new pure helper, `mergeRemovedSocialEvents` (`lib.ts`, tested in
   `lib.test.ts`), which re-sorts by `start_at` so a restored event lands
   back at its original chronological slot. The row renders struck-through
   title + dimmed "Removed · Undo"; Undo issues
   `PATCH { is_social: true }`, drops the id from `removed`, and refreshes.
   `removed` intentionally is not persisted past the current day — it's
   reset whenever `data.date` changes (day nav), matching the requirement
   that Undo only needs to survive until the user leaves the day.
4. **Subtotals/detections stay correct with no extra bookkeeping.** Because
   the backend already excludes a `user_is_social = false` event from
   `/today`, `subtotalsFromDay(data)` (which reads `data.social_events`
   directly, not the merged list) automatically drops the removed event's
   spend the moment `refresh()` lands — no client-side subtraction logic
   needed. The one thing that does need the merged list is the "Noticed
   quietly" empty-state check (`detections`), which now counts
   `socialRows.length` (merged) instead of `data.social_events.length` so a
   "Removed · Undo" row doesn't coexist with a "Nothing this day" message.
5. The existing "Counts as social" checkbox + Save flow is untouched — this
   adds a second, faster path for the specific "this didn't happen" case,
   it doesn't replace the general override path.

## Rejected Alternatives

- **One-tap × on the row itself.** Fastest to build, but a stray tap on a
  dense list of quiet rows silently removes an event with no confirmation
  step, and a visible × on every row is the "loud" clutter the quiet-list
  aesthetic explicitly avoids. The edit-form button keeps the destructive
  action behind one deliberate tap into the form, same cost as today's
  checkbox flow, just relabeled and undoable.
- **Affordance-only, no explicit action.** Adding the chevron/hover alone
  fixes discoverability but leaves removal exactly as buried as before —
  still checkbox-shaped, still framed as "fix a wrong classification" rather
  than "this got canceled." Doesn't address the actual reported friction.
- **Refetch-based undo (no local `removed` state).** Considered relying on
  `refresh()` plus some "undo window" server flag, but the day query has no
  concept of a recently-removed-but-undoable event, and adding one would be
  a backend change this design explicitly avoids (the PATCH route already
  does everything needed). Local component state is sufficient given the
  Undo affordance only needs to survive within the same day view.
