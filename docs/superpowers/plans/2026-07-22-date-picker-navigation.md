# Tap-to-Pick Date Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tapping the date label on Today or the week label on Scorecard opens a native calendar picker to jump directly to any past day/week.

**Architecture:** An invisible native `<input type="date">` overlays the `.nav-label` block inside the existing `DayNav`/`WeekNav` components; both gain an `onPick(iso)` prop. A pure `mondayOf(iso)` helper in `lib.ts` maps picked dates to week starts for the Scorecard.

**Tech Stack:** React + TypeScript, vitest. No backend changes, no new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-22-date-picker-navigation-design.md`

## Global Constraints

- No new frontend dependencies; native date input only, no `showPicker()` calls.
- Picker `max` prevents future selection; onChange additionally ignores empty values and values `> max`.
- Snap-to-null semantics preserved: picking today → `selected = null`; picking a date in the current week → `weekStart = null`.
- Only existing OKLCH tokens in CSS (`--muted` for the underline cue).
- Frontend checks: `cd frontend && npm test -- --run && npm run build`. No commits with failing checks.

---

### Task 1: `mondayOf` helper in `lib.ts`

**Files:**
- Modify: `frontend/src/lib.ts`
- Test: `frontend/src/lib.test.ts`

**Interfaces:**
- Consumes: existing `parseDay`, `addDays` in `lib.ts`.
- Produces: `mondayOf(iso: string): string` — ISO date of the Monday of the week containing `iso` (Sunday maps back to the previous Monday).

- [ ] **Step 1: Write the failing tests**

Append to `frontend/src/lib.test.ts`:

```ts
import { mondayOf } from "./lib";

describe("mondayOf", () => {
  it("maps a mid-week date to its containing Monday", () => {
    expect(mondayOf("2026-07-22")).toBe("2026-07-20"); // Wednesday
  });
  it("is identity on a Monday", () => {
    expect(mondayOf("2026-07-20")).toBe("2026-07-20");
  });
  it("maps Sunday back to the previous Monday", () => {
    expect(mondayOf("2026-07-26")).toBe("2026-07-20");
  });
  it("crosses year boundaries", () => {
    expect(mondayOf("2026-01-01")).toBe("2025-12-29"); // Thursday
  });
});
```

Fold the import into the existing `from "./lib"` import line if the file has consolidated them; otherwise a separate import statement is acceptable.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npm test -- --run`
Expected: FAIL — `mondayOf` is not exported.

- [ ] **Step 3: Implement**

Append to `frontend/src/lib.ts`:

```ts
export function mondayOf(iso: string): string {
  const d = parseDay(iso);
  return addDays(iso, -((d.getDay() + 6) % 7));
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npm test -- --run`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib.ts frontend/src/lib.test.ts
git commit -m "feat(frontend): mondayOf week-start helper"
```

---

### Task 2: Invisible picker inputs + wiring

**Files:**
- Modify: `frontend/src/components/DayNav.tsx`
- Modify: `frontend/src/components/WeekNav.tsx`
- Modify: `frontend/src/screens/Today.tsx`
- Modify: `frontend/src/screens/Scorecard.tsx`
- Modify: `frontend/src/styles.css` (append)

**Interfaces:**
- Consumes: Task 1's `mondayOf`; existing `selected`/`todayIso` state in Today.tsx and `weekStart`/`currentWeekStart` state in Scorecard.tsx.
- Produces: `DayNav` props gain `onPick: (iso: string) => void`; `WeekNav` props gain `onPick: (iso: string) => void` and `max: string`. Scorecard tracks `currentWeekEnd` (from the current week's card) as the picker's `max`.

- [ ] **Step 1: Update `DayNav.tsx`**

Add `onPick` to `Props` and the input inside `.nav-label`:

```tsx
interface Props {
  date: string;
  todayIso: string;
  onPrev: () => void;
  onNext: () => void;
  onPick: (iso: string) => void;
}
```

```tsx
      <div className="nav-label">
        <input
          className="nav-pick"
          type="date"
          value={date}
          max={todayIso}
          aria-label="Pick a date"
          onChange={(e) => {
            const v = e.target.value;
            if (v && v <= todayIso) onPick(v);
          }}
        />
        <h2>{relativeDayLabel(date, todayIso)}</h2>
        <p className="sub">{dayLabel(date)}</p>
      </div>
```

- [ ] **Step 2: Update `WeekNav.tsx`**

```tsx
interface Props {
  weekStart: string;
  isCurrent: boolean;
  max: string;
  onPrev: () => void;
  onNext: () => void;
  onPick: (iso: string) => void;
}
```

```tsx
      <div className="nav-label">
        <input
          className="nav-pick"
          type="date"
          value={weekStart}
          max={max}
          aria-label="Jump to a week"
          onChange={(e) => {
            const v = e.target.value;
            if (v && v <= max) onPick(v);
          }}
        />
        <h2>{isCurrent ? "This week" : "Week of"}</h2>
        <p className="sub">{weekLabel(weekStart)}</p>
      </div>
```

- [ ] **Step 3: Wire `Today.tsx`**

Add to the `<DayNav>` element:

```tsx
        onPick={(iso) => setSelected(iso === todayIso ? null : iso)}
```

- [ ] **Step 4: Wire `Scorecard.tsx`**

Add state and capture the current week's end alongside its start:

```tsx
  const [currentWeekEnd, setCurrentWeekEnd] = useState<string | null>(null);
```

In the scorecard fetch `.then`, where `currentWeekStart` is set:

```tsx
        if (!weekStart) {
          setCurrentWeekStart(c.week_start);
          setCurrentWeekEnd(c.week_end);
        }
```

Add `mondayOf` to the `../lib` import. Add to the `<WeekNav>` element:

```tsx
        max={currentWeekEnd ?? card.week_end}
        onPick={(iso) => {
          const monday = mondayOf(iso);
          setWeekStart(monday === currentWeekStart ? null : monday);
        }}
```

(`max` is the current week's Sunday: any pick inside the current week snaps to `null`, and days after today within the current week are harmless — they map to the same week.)

- [ ] **Step 5: Append CSS to `frontend/src/styles.css`**

```css
/* ── Tap-to-pick date navigation ── */
.nav-label { position: relative; }
.nav-label h2 {
  text-decoration: underline dotted;
  text-decoration-color: var(--muted);
  text-underline-offset: 4px;
}
.nav-pick {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  opacity: 0;
  border: 0;
  padding: 0;
  cursor: pointer;
}
```

- [ ] **Step 6: Verify**

Run: `cd frontend && npm test -- --run && npm run build`
Expected: all tests pass, build clean.
Manual: tap the Today date label → OS calendar opens, future dates disabled, picking a past day navigates there; tap the Scorecard week label → picking any date jumps to that week; picking today/current week returns to the live view (streaks visible again).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/DayNav.tsx frontend/src/components/WeekNav.tsx frontend/src/screens/Today.tsx frontend/src/screens/Scorecard.tsx frontend/src/styles.css
git commit -m "feat(frontend): tap date/week labels to open native calendar picker"
```
