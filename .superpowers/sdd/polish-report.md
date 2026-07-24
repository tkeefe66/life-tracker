# Money screen — polish pass report

Branch `worktree-money-design-pass`, on top of layout pass `0816269`. Source: backlog
P2/P2-minor/minor items assigned in the task brief, cross-checked against the critique
snapshot `2026-07-24T22-20-22Z__frontend-src-screens-money-tsx.md` and
`/impeccable polish` reference. CSS-only + one two-token TSX fix; no markup renamed,
no IA change, no copy rewrite.

## 1. [P2] Pill/button vocabulary consolidation — DONE

Added a shared pill primitive to `:root` in `styles.css`:

```css
--pill-pad: 4px 12px;
--pill-pad-compact: 1px 8px;
--pill-radius: 999px;
--pill-fs: 0.8rem;
--pill-border: 1px solid var(--line);
```

Every interactive pill on the Money screen now consumes these instead of a private
literal: `.vendor-chip`, `.vendor-label-btn`, `.vendor-bulk-offer`, `.audit-btn`, the
triage queue's choice-chip override (`.triage-queue .chips button`), and
`.suggest-badge`. Kept exactly two padding tiers, both tokenized:

- **Standard** (`--pill-pad`, 4px 12px) — standalone tap targets: vendor filter chips,
  audit confirm/change/reject buttons, the vendor bulk-offer row. `.vendor-bulk-offer`
  moved from a private `3px 10px` onto this.
- **Compact** (`--pill-pad-compact`, 1px 8px) — pills that sit inline next to text
  rather than standing alone: the vendor drill-down's label button and the
  `suggest-badge` annotation. This is the one deliberate padding exception, and it's
  now a named token rather than two more private literals.

Deliberate distinctions kept, exactly as scoped — carried by color/border only, never
by re-deriving size:
- **Selected vs unselected**: `.vendor-chip-on` (border/color → `--ink`) vs base
  `.vendor-chip` (muted/transparent) — untouched.
- **Primary vs quiet**: `.audit-btn-primary` (filled `--accent`) vs `.audit-btn`
  (outline) — untouched.
- **Suggested vs confirmed**: see item 2 below.
- **Add/edit affordance**: `.vendor-label-btn` keeps its dashed border (a real,
  separate signal — "click to set/change this," independent of suggested-or-not) —
  only its padding/radius/font-size moved onto the shared tokens.

### Before → after per class

| Class | Before | After |
|---|---|---|
| `.vendor-chip` | `4px 12px` / `999px` / `0.85rem` / `1px solid var(--line)` | tokens (font-size now `0.8rem`, everything else identical) |
| `.vendor-label-btn` | `1px 8px` / `999px` / `0.8rem` / `1px dashed var(--line)` | `--pill-pad-compact` / `--pill-radius` / `--pill-fs` / dashed border kept explicit (deliberate) |
| `.vendor-bulk-offer` | `3px 10px` / `999px` / `0.8rem` / `1px solid var(--line)` | `--pill-pad` (bumped from 3px→4px vert, 10px→12px horiz) / tokens |
| `.audit-btn` | `4px 12px` / `999px` / `0.8rem` / `1px solid var(--line)` | tokens (values unchanged, now shared not private) |
| `.suggest-badge` | `1px 8px` / `999px` / `0.72rem` | `--pill-pad-compact` / `--pill-radius` / `--pill-fs` (font-size bumped 0.72rem → 0.8rem) |
| `.triage-queue .chips button` | inherits base `.chips button`'s `10px` radius (square, built for single-digit alcohol chips) + `0.9rem`/600 font | explicit `--pill-radius` (999px, now a real pill) + `--pill-fs` (0.8rem); padding (`8px 12px`, bigger touch target) and font-weight 600 kept as a deliberate primary-decision-chip distinction |

Scoping note: the triage override lives on `.triage-queue .chips button`, so nothing
about Today's alcohol-level chips (`.chips button` base, 38×38 square) changed —
verified by re-reading `Today.tsx`'s only other `.chips` consumer before touching the
selector.

## 2. [P2] ONE "suggested" encoding — DONE

Collapsed three different treatments onto one literal signature everywhere:
`background: var(--accent-soft); color: var(--accent);` — no border tint, no italic,
no muted-text fallback.

| Site | Before | After |
|---|---|---|
| `.suggest-badge` | `background: var(--accent-soft); color: var(--accent);` | unchanged (this was already the reference signature — "badge keeps it," per the task brief) |
| `.vendor-label-suggested` (drill-down suggested label button) | `font-style: italic; color: var(--muted);` | `background: var(--accent-soft); color: var(--accent);` — italic and muted-text both dropped; stays a real button (`.vendor-label-btn`'s shape/padding untouched) |
| `.triage-queue .chips button.chip-suggested` | `background: var(--accent-soft); border-color: color-mix(in oklch, var(--accent) 35%, var(--line));` (bg-only, no text-color change, plus a one-off border tint) | `background: var(--accent-soft); color: var(--accent);` — border-color mix removed, `color: var(--accent)` added, now textually identical to the other two |

All three rules are now the same two declarations, not just visually similar — one
encoding, not three converging by coincidence.

## 3. [P2-minor] Unify the worklist card ground — DONE

`.audit-card` moved from `background: var(--surface-2)` to `background: var(--surface)`,
matching `.triage-queue` (which was already on `--surface` and, per the brief, "there
first"). Border, radius, padding, and shadow on `.audit-card` were left untouched — only
the ground token changed, so both decision-zone worklists (triage + label audit) now
sit on the same surface.

## 4. [minor] Explicit refund null-check — DONE

`Money.tsx`:
```diff
- {summary.totals.refund?.amount > 0 && (
+ {(summary.totals.refund?.amount ?? 0) > 0 && (
```
Matches the repo's "null-check, never truthiness" money rule literally — previously
correct only because `undefined > 0 === false` in JS, which is implicit and easy to
break on a future refactor (e.g. a default of `null` instead of `undefined` would still
work here by luck, but the intent wasn't visible in the code).

## 5. [minor] `.meter i` transition: width → transform — SKIPPED, in scope but blocked

Investigated: the only place `.meter i`'s width is set is `Today.tsx` (`<i
style={{width: ...}}/>` in the "This week" strip) — not a Money component at all. A
transform-based scale requires the JS side to compute and set `transform:
scaleX(ratio)` instead of a percentage width, which means editing `Today.tsx`'s inline
style, not just `styles.css`. Per the task's own conditional ("ONLY if the change is
contained to CSS + doesn't break the bar's rounded ends visually; if it needs markup
changes in other screens' components, leave it and note why") — this needs a markup
change in a screen outside the Money surface I own for this pass, so I left it. The
critique snapshot itself notes this is "app-wide, not Money's" and "marginal impact,"
consistent with leaving it for whoever next touches `Today.tsx`/the meter component
directly.

## 6. Anything else at this altitude

Checked and found no further gaps in scope:
- **Focus states**: every pill touched here (`.vendor-chip`, `.vendor-label-btn`,
  `.vendor-bulk-offer`, `.audit-btn`, triage chips, `.suggest-badge` is non-interactive)
  is covered by the global `:focus-visible` rule (styles.css:82) — no per-component
  focus ring was missing.
- **Reduced motion**: nothing in this pass introduced new animation; the global
  `prefers-reduced-motion: reduce` kill switch (styles.css ~624) already covers the
  existing `.settle` pulse and meter transition untouched by this pass.
- **Hover/active**: no pill in this family had a hover/active state before this pass
  (rows use `:active` scale via the shared `button` reset); none were regressed, and
  adding new interaction states was out of scope for a token-consolidation pass — flagging
  for a future pass rather than inventing new states without design review.
- Deliberately did **not** touch `.chip`/`.chip-accent`/`.chip-over` (WeekDays' day
  tags) — those are non-interactive info pills, explicitly called out in the existing
  CSS comment as intentionally distinct from the vendor-chip family, and out of the
  backlog's scope for this pass.
- Did **not** touch `button.quiet-btn` ("Apply to the other N... charges" / social-form
  quiet actions) — not one of the 6 pill treatments named in the backlog, and it's a
  full-width block style used by other screens too, not a pill.

## Verification

```
npm test -- --run   → 73/73 passed (Test Files 2 passed)
npx tsc --noEmit    → clean, no errors
npm run build       → succeeded (vite build, 49 modules, no warnings)
```

No browser available (Chrome extension blocks localhost) — all visual claims above are
described per-class in the before/after tables rather than screenshotted.

## Judgment calls

1. `.vendor-chip`'s font-size moved 0.85rem → 0.8rem (a real, if small, visual change)
   to land exactly on the shared token rather than adding a third font-size tier for a
   1-of-6 outlier.
2. `.suggest-badge`'s font-size moved 0.72rem → 0.8rem for the same reason — it's an
   inline annotation, not a standalone button, so it kept the compact *padding* tier
   but not a separate *type* tier, per the brief's "one set of pill metrics."
3. `.vendor-bulk-offer` padding bumped slightly (3px 10px → 4px 12px, the standard
   tier) rather than added as a third one-off value or folded into the compact tier —
   it's a standalone `display: block` action row, not an inline annotation, so it
   belongs with the standard tier's other standalone actions.
4. Triage's choice-chip padding (`8px 12px`) and `font-weight: 600` were kept
   deliberately larger/bolder than the rest of the pill family — these are the
   queue's primary decision action, already documented in the existing CSS comment as
   sized for multi-word labels and a bigger touch target, which reads as the
   "primary" distinction the brief said to keep, not a leftover divergence to fix.
5. `chip-suggested`'s border-color mix was dropped entirely (not merged into a new
   shared "suggested border" token) since neither `.suggest-badge` nor
   `.vendor-label-suggested` had a border treatment to align to — the literal
   accent-soft-bg + accent-text pair is the whole signature now, with no fourth
   property invented to preserve.

## Left for later (not done, and why)

- Item 5 above (`.meter i` transform) — needs `Today.tsx` markup change, outside this
  pass's Money-only surface.
- New hover/active states for the newly-shared pill family — not previously present,
  not named in the backlog, would need its own design pass rather than being invented
  during a token-consolidation polish.
