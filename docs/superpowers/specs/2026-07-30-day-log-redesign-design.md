# Day Log Redesign — Design

**Date:** 2026-07-30
**Status:** Approved in visual brainstorm (mockups preserved in `2026-07-30-day-log-mockups/` —
open them in a browser; they were built live with real Jul 24 data during the session).

## Problem

The "Noticed quietly" section outgrew its name. It began as passive detection output;
it is now the interactive center of the Day screen — editing, removal/undo, the
"social?" ambiguity chip — and it is about to absorb more (bank rows, night-out
context). Specific failures observed on real data (2026-07-29 session):

- The "social?" Yes/No chip rendered as a floating line with no visual attachment
  to its event row.
- The edit form showed a pre-checked "Counts as social" checkbox for an event the
  system was explicitly *unsure* about — two controls answering one question,
  disagreeing about certainty.
- Rows gave no indication of type or scoring relevance: nothing distinguishes a
  scored social event from unscored ride spend, or says whether "Adi Bday Downtown"
  counts.
- Meta text ordering was `$ · time`; the user reads time-then-amount.
- Verbose Gmail-derived calendar titles ("Spider-Man: Brand New Day Amazon Prime
  Early Access Screenings (2026)") dominate the row.

## Decisions

### 1. The timeline is the only structure

The Day log renders one chronological feed of the day — detected spend rows,
social events, and check-ins (e.g. the alcohol level, once logged) in time order.
Section heading: **"Day log"** (replaces "Noticed quietly").

**Rejected — grouped-by-kind layout:** grouping already exists where it earns its
keep (Money screen = spend grouped by kind/label; Week screen = grouped by metric).
At day scale, groups hold 1–3 rows each and destroy the temporal story that only
this screen can tell (the night-out arc: party → failed pickups → 2:34 AM ride home).

**Rejected — timeline ⇄ grouped toggle** (was initially requested, then reversed
after discussion): every future day-log feature would need designing twice; toggles
hide the unpicked view; the grouped read is one tab away. The user's deciding
realization: "it is only one day."

### 2. Tap-to-filter category strip

A small strip of category icons above the feed; tapping one filters the day to
that category. Filtered-out rows **dim, they do not vanish** — the day's shape
stays visible. The strip shows only categories present on that day (typically 2–4).

### 3. Categories are a closed set — the cap is a rule

Exactly six, ever: **food, transport, social, drink, fitness, money.**
Adding a seventh is a deliberate design event with the same weight as adding a
chart color (see "Chart colors are validated, not chosen" in CLAUDE.md). All
anticipated growth (bank spending types, labels, vendors) lands in the *label*
tier, never as new categories. This cap is what keeps the filter strip and icon
vocabulary sane at scale — it IS the design.

### 4. Three-tier marking: icon / chips / text

| Tier | Vocabulary | Rendering |
|---|---|---|
| Category | bounded (6) | emoji glyph only — **no color fields** (mockup option B) |
| Status (scoring) | tiny fixed set | word chips: `social`, inline `social? Yes/No`, `Removed · Undo` |
| Labels/vendors | unbounded | plain text, no color, no icons, ever |

**Rejected — category background colors** (option A): survives scale only via the
same cap, but adds a color-maintenance burden (dark theme steps, colorblind
validation) for marginal scannability. **Rejected — words-for-everything**
(option C): tag stacking crowds rows.

### 5. Icons encode source, never inference

A delivery-app order is food (🍔) because it came from a delivery service — full
stop. Drinking is tracked by the user's check-in, never guessed from a charge:
a future bank bar-tab row is *money* + its label text, not 🍺. When alcohol
inference arrives (see Future work) it surfaces as a quiet **question** on the
day ("late night at The Cooper Lounge — drinking?") that feeds the existing
check-in — never as a system-assigned icon or auto-logged level. No icon ever
claims metric membership; chips do that.

### 6. Row anatomy

`[icon] name ……… [status chip] time · $amount ›`

(Chip inside the meta, so amounts right-align down the list — revised from
chip-outermost after seeing it live on 2026-07-30.)

- Time sits **left of the amount** (`7:37 PM · $20.93`).
- Money formatting unchanged (`$16.31`, `$20`, real `$0` shows).
- Ride/true-time and effective-date behavior unchanged (they ship already).
- Long titles truncate with ellipsis; the existing `user_title` rename in the
  edit form is the escape hatch.

### 7. Ambiguity chip and edit form reconciliation

- The `social? Yes / No` chip renders **inside its event's row** (right-aligned,
  where a `social` chip would sit), not as a detached line.
- While an event is unanswered-uncertain, the edit form does **not** show the
  "Counts as social" checkbox (a checked box asserts certainty the system lacks;
  the chip is the answer surface). Once answered — or for confident events — the
  checkbox appears as today, meaning "correct the classification."
- "Didn't happen" / Undo behavior unchanged.

### 8. Week tab: consistency by shared vocabulary, not restructure

The Week screen keeps its aggregate character (metric cards, trends, spend
subtotals). Consistency is achieved by sharing primitives, not layouts:

- Extract `CategoryIcon` and `StatusChip` as reusable components; the Day log and
  Week screen both consume them.
- `SpendSubtotals` rows on the Week screen gain the same category glyph
  (🍔 Uber Eats, 🚗 Uber rides); their meta stays amount-only since subtotals
  have no times.
- Anything larger on the Week tab is out of scope; if a Week redesign happens
  later it inherits this vocabulary for free.

## Data fixes shipping with this change

- Clear the accidental `$25` amount on the Spider-Man event (2026-07-29) — it was
  saved through the edit form during testing, not parsed from anywhere.

## Future work (recorded, deliberately not built now)

- **Night band:** visually connect the late-night arc (post-cutoff rows) to the
  previous evening — groundwork exists via effective-date attribution.
- **Alcohol inference:** suggest (never auto-log) an alcohol check-in from
  late-night venue charges + social-event adjacency. Start rule-based
  (time window + label/payee keywords, no AI). If AI is ever involved, note the
  privacy boundary: bank payees currently reach Claude only via
  `suggest_bank_flows` — widening that is a deliberate decision (see
  2026-07-30-social-classification-granularity spec, same note).
- **Bank rows in the Day log:** date-only transactions need placement rules
  (no time → end-of-day cluster or dateless shelf) before they join the timeline.
- **Event–spend association** ("Adi Bday Downtown" ↔ that night's charges).

## Implementation notes

Frontend-only except the data fix. Componentize: `DayLogRow`, `CategoryIcon`,
`StatusChip`, `FilterStrip`. Pure helpers (category assignment per row kind,
filter logic, meta formatting) live in `lib.ts` with vitest coverage. Both themes;
no fixed pixel chart rules apply here but the same restraint does. No backend
schema changes.
