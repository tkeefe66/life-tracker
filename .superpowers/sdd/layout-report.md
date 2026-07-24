# `/impeccable layout` — Money screen — report

Branch: `worktree-money-design-pass`, worktree `.claude/worktrees/money-design-pass`.

## What changed, per critique item

### 1. Section order (P1)
`Money.tsx`: moved the `VendorBreakdown` render call above the movement-flows
block ("Where the rest went"). New order in the `bankSectionsVisible` fragment:
hero → `BankSpendChart` → `VendorBreakdown` ("Where it went") → "Where the rest
went" → "Money in". Pure JSX reorder — no props, state, or data-fetch code
touched. "Money in" still renders after movements, as instructed.

### 2. Heading semantics
- Added a `.visually-hidden` utility to `styles.css` (standard clip-rect
  pattern — absolutely positioned, 1×1px, clipped, not `display:none`, so it
  stays in the accessibility tree and in heading navigation).
- Added `<h1 className="visually-hidden">Money</h1>` as the **first child of
  Money's root `<div>`**, unconditionally — not only inside the
  `bankSectionsVisible` branch. Judgment call, see below.
- Converted every `<p className="section-label">` to `<h2 className="section-label">`
  across the app: `Money.tsx` (2), `VendorBreakdown.tsx` (1), `LabelAudit.tsx`
  (1), `SpendSubtotals.tsx` (1 — shared by Money/Scorecard/Today), `Today.tsx`
  (2), `Insights.tsx` (4), `Settings.tsx` (6, alongside its pre-existing
  `<h2>Settings</h2>` title). Confirmed the CSS selector is `.section-label`
  (class-based, not `p.section-label`) before touching it — zero visual
  change, verified by `vite build` succeeding and no CSS edits needed for this
  item beyond the new utility class.
- `TriageQueue`'s internal `<h3>{title}</h3>` (used twice inside the "Needs a
  decision" `<h2>` zone) already nests correctly under the new `<h2>` — no
  change needed there.

### 3. Hierarchy of the lower two-thirds (P2-adjacent)
Added a `.zone-break` CSS rule (compound selector `.zone-break.section-label`,
so its higher specificity wins over the plain `.section-label` margin without
depending on stylesheet order): `margin-top: 56px; padding-top: 20px;
border-top: 1px solid var(--line);`. Applied only to the `<h2>` reading "Needs
a decision" — the single entry point into the decision zone (the two
`TriageQueue`s + "Recently sorted" + `LabelAudit`'s "Suggested labels — needs a
look"). No cards, no new colors, nothing collapsed; both triage queues and the
label audit remain fully visible exactly as before.

### 4. Working-memory bridge (P2)
`Money.tsx`: `SpendSubtotals`'s title for the bank-visible case changed from
"Of that, the things you're tracking" → "The things you're tracking". The
fallback title ("By service · last 12 weeks", shown when bank sections aren't
visible) is untouched.

## Verification

```
npm test -- --run        → 73/73 passed (2 test files) — matches the stated baseline
npx tsc --noEmit          → clean, no errors
npm run build             → succeeds (tsc --noEmit && vite build), dist/ emitted
```
Backend untouched (no Python files touched).

## Judgment calls

1. **`<h1>` placement.** The spec said "the hero amount area gets an
   `<h1 class="visually-hidden">Money</h1>`," but the hero only renders inside
   `bankSectionsVisible`. Placing the h1 only there would leave the screen
   headingless in the "bank not configured" and "awaiting first sync" states
   (both real, common states for this single-user app before/between syncs).
   I placed the h1 unconditionally as the screen's first element instead —
   still describes/labels the same "Money" hero content, and gives every
   screen state a heading for screen-reader navigation. Flag if you wanted it
   scoped strictly to the `bankSectionsVisible` branch.
2. **Section-label swap scope.** Confirmed mechanical (class-based CSS,
   verified via `grep` before editing) and applied it everywhere the class is
   used, not just Money — per the instruction's "ONLY if... mechanical."
   `SpendSubtotals` is shared by Today/Scorecard/Money, so one component edit
   covers three screens.
3. **Settings.tsx now has multiple sibling `<h2>`s** ("Settings" the page
   title, plus six section `<h2>`s) rather than a nested h1→h2 outline. This
   pre-dates the change (the page title was already `<h2>`, not `<h1>`) — the
   swap doesn't make Settings' outline worse, just doesn't fix its pre-existing
   flat structure. Out of scope per the instructions (Settings' own page-title
   heading level wasn't part of this backlog item).
4. **No h1 added to Today/Scorecard/Insights/Settings.** Scope explicitly
   limited the h1 to Money's hero; those screens keep whatever heading
   structure they had before (Settings' `<h2>Settings</h2>`; the others, none).
5. Did not touch `.meter i`'s width-based transition (critique's minor
   detector note) — out of scope for this pass.

## Files touched

- `frontend/src/styles.css` — `.visually-hidden` utility, `.zone-break` rule
- `frontend/src/screens/Money.tsx` — reorder, h1, h2 swaps, zone-break class, retitle
- `frontend/src/components/VendorBreakdown.tsx` — h2 swap
- `frontend/src/components/LabelAudit.tsx` — h2 swap
- `frontend/src/components/SpendSubtotals.tsx` — h2 swap (shared)
- `frontend/src/screens/Today.tsx` — h2 swap (×2)
- `frontend/src/screens/Insights.tsx` — h2 swap (×4)
- `frontend/src/screens/Settings.tsx` — h2 swap (×6)
