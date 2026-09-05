# Implementation brief — matrix rebuild, Friday semantics, `shabbat_observant`

Hand this to Claude Code as-is, alongside `SPEC.md`.

Four parts, in this order. Part 1 is independent. Part 2 is a bug fix that
Part 3 then generalises — do not merge them into one change, and do not start
Part 3 before Part 2's tests pass. Part 4 is frontend only.

Do **not** touch: `planner/rule_based.py`'s search algorithm, the seed data,
the scrapers, or anything under `api/scripts/` other than `build_matrix.py`.

When finished, run `python scripts/verify_shabbat_mode.py` from the repo root.
It is the machine-checkable half of "definition of done" and it must exit 0.

---

## Part 1 — `build_matrix.py` reads the region directory

### The bug

`api/scripts/build_matrix.py` still defines
`DEFAULT_PLACES_PATH = DATA_DIR / "places.json"`. That file was split into
`api/data/places/{north,central,south}.json` (SPEC section 12) and no longer
exists, so the script cannot run at all and the committed
`distance_matrix.json` is stale: **14 of the 37 seeded places have no row**,
and the `south` region currently has zero same-region pairs at any leg cap,
so every southern request falls through to section 4 fallback step 2.

### The change

1. Replace `--places <file>` with `--places-dir <dir>`, defaulting to
   `api/data/places/`, honouring `$PLACES_DIR` the same way
   `repository/places.py` does (explicit arg → env var → default). Keep the
   resolution order identical to `PlaceRepository.load()` so the matrix and
   the API can never read different seeds.
2. Load through `PlaceRepository.load(...).all()` rather than re-implementing
   directory reading. The repository already fails loudly on a misfiled or
   duplicated row (SPEC section 12); the matrix builder must inherit that,
   not bypass it.
3. Leave `build_matrix()`, `haversine_km()` and `estimate_drive_minutes()`
   exactly as they are. Same numbers, different source file.
4. Keep the existing orphan warning and extend the summary line to print, per
   region, the number of places and the number with at least one neighbour at
   each of the three caps (20 / 45 / 90). That table is how a stale matrix
   gets noticed next time.

### Then run it

```
python -m api.scripts.build_matrix
```

and commit the regenerated `api/data/distance_matrix.json` as a **separate
commit** from the code change, so the diff of generated data is reviewable on
its own.

### Tests — `api/scripts/test_build_matrix.py` (new file)

- building from a two-region fixture directory produces no cross-region cells
- a place with `lat is None` gets no cells and is reported as an orphan
- the matrix is symmetric: `m[a][b] == m[b][a]` for every pair
- `MIN_LEG_MINUTES` floors a pair of near-identical coordinates
- the builder reads the same directory `PlaceRepository` would, given
  `$PLACES_DIR`

---

## Part 2 — Friday is a deadline, not a filter on closing time

### The bug

`shabbat.is_shabbat_eligible` implements rule 12's Friday half as *"the place
must close before 15:00"*. That is wrong twice over:

1. An `access == "open"` place has seven `null` opening hours **by design**
   (SPEC section 10) — there is no gate for a weekly schedule to describe. The
   current code reads that `null` as "closed on Friday" and drops it. All 14
   open places in the seed are excluded every Friday.
2. Even for a `gated` place, "closes by 15:00" is stricter than the rule
   needs. A museum open 09:00–18:00 on Friday can perfectly well be visited
   09:00–11:00 with the user home long before Shabbat. The rule the product
   actually wants is *the visit ends by 15:00*, not *the place shuts by 15:00*.

Measured on the current seed, places available on Friday:

| region  | today | after this fix |
|---------|-------|----------------|
| north   | 2     | 12             |
| central | 3     | 15             |
| south   | 0     | 5              |

Since `web/src/weekday.ts` derives the weekday from today's date, anyone who
opens the app on a Friday currently sees the fallback screen.

### The change — one new private helper in `domain/schedule.py`

Introduce the effective bookable window as the single place the Friday
deadline is applied, and express every existing hours check in terms of it:

```python
def _effective_window(
    place: Place,
    weekday: Weekday,
    shabbat_observant: bool = True,
) -> tuple[str, str] | None:
    """
    The window this place can actually be visited in on this weekday, or None
    when it cannot be visited at all.

    Three things collapse into one pair here, which is why every other hours
    check delegates to it rather than re-deriving them:

      * a `gated` place's own opening_hours for the weekday (None => closed),
      * an `open` place's fixed daylight window (it has no gate to describe),
      * rule 12's Friday deadline, applied as a *clamp on the close time*
        rather than as a rejection of the place.
    """
```

Behaviour:

- `access == "open"` → `(DAYLIGHT_START, DAYLIGHT_END)`.
- `access == "gated"` → `opening_hours[weekday]`, or `None` when that is null.
- Then, when `shabbat.friday_deadline(weekday, shabbat_observant)` returns a
  time, clamp the close time to `min(close, deadline)`. Zero-padded `"HH:MM"`
  compares correctly as a string; keep it that way and say so in a comment.
- Return `None` if the clamped window has `opens >= closes`.

Rewrite in terms of it, changing nothing else about their contracts:

- `is_open_at(place, weekday, at, shabbat_observant=True)` — window is None →
  `False`; else `opens <= at < closes`.
- `visit_fits_opening_hours(place, weekday, arrive_at, shabbat_observant=True)`
  — window is None → `False`; else
  `opens <= arrive_at and arrive_at + duration_min <= closes`.
- `is_available_on(place, weekday, month, shabbat_observant=True)` — keep the
  unverified-gated check and the season check exactly as they are, then
  require that `_effective_window(...)` is not None **and** wide enough for
  `place.duration_min`. A gated place open 16:00–20:00 on Friday is now
  correctly unschedulable for an observant user without a special case.

### `domain/shabbat.py`

Friday stops being an eligibility question and becomes a deadline. Replace the
Friday branch of `is_shabbat_eligible` with a new function and shrink the old
one to the Saturday rule it now solely owns:

```python
def friday_deadline(weekday: Weekday, shabbat_observant: bool = True) -> str | None:
    """Latest a visit may end, or None when no deadline applies to this day."""

def is_shabbat_eligible(place: Place, weekday: Weekday) -> bool:
    """Rule 12's Saturday half: closed_on_shabbat is never scheduled on Saturday."""
```

`closed_on_shabbat` stays unconditional and applies to **every** user — it is
a fact about the place, not a preference of the person. Do not make it
conditional on anything in Part 3.

Update `shabbat_rejection_reason_he` to match: the Friday string becomes
`"לא ניתן לסיים את הביקור לפני 15:00"`, since the reason is no longer that
the place closes late.

Rewrite the module docstring. Right now it describes the old Friday rule and
would be actively misleading after this change.

### `validate_day` — rule 12

Saturday: unchanged. Friday: replace the per-stop eligibility loop with a
single check that the whole day ends by the deadline —
`to_minutes(day.ends_at) <= to_minutes(deadline)`. Times are strictly
increasing (rule 9), so every individual visit ending in time follows from
`ends_at` doing so; check the one thing rather than the seven.

### Tests — extend `api/domain/test_schedule.py` and `test_shabbat.py`

- an `access == "open"` place is schedulable on Friday (this is the
  regression test for the bug — assert it against a place with seven null
  hours)
- a gated place open Fri 09:00–18:00 with `duration_min = 120` is schedulable
  at 09:00 and not at 14:00
- a gated place open Fri 16:00–20:00 is not schedulable at all
- `validate_day` reports a rule 12 violation for a Friday day ending at 15:30
  and none for one ending at 14:45
- Saturday behaviour is byte-for-byte unchanged: add a test asserting a
  `closed_on_shabbat` place is rejected on Saturday and accepted on Thursday
- Sunday–Thursday impose no deadline: `_effective_window` returns the
  unclamped hours

---

## Part 3 — `shabbat_observant`, a request field

### What this is, and what it is not

This is a **planner input**, not an LLM feature: a boolean on the request that
selects which of two documented rule-12 behaviours applies. There is no
`planner/llm.py` in this repo yet, and this brief does not add one. When it
lands, its job will be to map free Hebrew text ("אנחנו שומרי שבת") onto this
field — exactly as it will for `region`, `with_meal` and the rest. Adding the
field now is what makes that later mapping a one-line change instead of a new
rule.

### Semantics — Friday only, deliberately

| | `shabbat_observant: true` (default) | `false` |
|---|---|---|
| Fri | every visit ends by 15:00; day `ends_at <= 15:00` | ordinary weekday; real hours / daylight apply |
| Sat | `closed_on_shabbat` places excluded | identical — same exclusion |
| Sun–Thu | no effect | no effect |

Two decisions to record in the SPEC amendment rather than leave implicit:

**Default is `true`.** The two failure modes are not symmetric. Defaulting to
`false` and being wrong schedules an observant user into Shabbat; defaulting
to `true` and being wrong gives a secular user a Friday that ends earlier than
it had to. The second is recoverable with one tap. It also preserves today's
behaviour, so the change is additive.

**Saturday is deliberately untouched.** Whether an observant user will travel
on Shabbat at all is not something this app can answer: there is no origin, no
location and no way to build a walking-distance day (SPEC section 5). The
factual filter — a place closed on Shabbat is closed for everyone — already
covers what the data actually knows. Write this down as a considered
non-decision; leaving it undocumented reads as an oversight.

### Threading it through

Add `shabbat_observant: bool = True` as a keyword argument, in this order:

1. `CreateItineraryRequest` (`models.py`), documented like `with_meal` is.
2. `Itinerary` (`models.py`) — it must be **persisted on the response**, because
   `remove` / `swap` / `undo` recompute a stored itinerary and have no request
   of their own. Same reason `max_leg_min` and `weekday` live there.
3. `ItineraryPlanner.plan(...)` (`planner/base.py`) + its docstring.
4. `RuleBasedPlanner.plan(...)` — pass it down to `candidates()` and to every
   `visit_fits_opening_hours` call. No change to the search itself.
5. `PlaceRepository.candidates(region, weekday, month, shabbat_observant=True)`.
6. `ItineraryEditor` — read it off the `Itinerary` it was handed, never from a
   default. An edit must not silently change the rule the day was built under.
7. `schedule.validate_itinerary` / `validate_day` — take it from the
   `Itinerary`, so `main.py::_reject_if_invalid` needs no new argument.

### Interaction with `day_length` — expected, must be tested

Friday + observant + a 09:00 start caps the day at 6.0 hours, which is below
the `"long"` band's 6.5h floor. `length_matched` will therefore be `false` for
every such request. This is the existing "preference degrades, never fails"
shape (SPEC section 13) working correctly, not a bug — add a test that asserts
exactly that, so nobody later "fixes" it by relaxing rule 10.

---

## Part 4 — Frontend: five rows, panel below the itinerary

Three changes, all in `/web`. The row count is the point of this part: adding
`shabbat_observant` as a sixth row is what forced the other two.

### 4a. Remove the start-time control, keep the capability

Delete `web/src/components/StartTimeChips.tsx`, the `startsAt` state, its
handler, and the `starts_at` argument from `planNew`'s call to
`createItinerary`.

Do **not** remove `starts_at` from `CreateItineraryRequest` in `models.py`,
from `ItineraryPlanner.plan`, or from `types.ts`'s request interface. The
server keeps its `"09:00"` default and the field stays in the contract. That
is the whole point: the control is cut, the capability is not, and `llm.py`
will be able to infer a start time from free text later without touching the
contract.

SPEC section 2 already names start time as the first control to cut if the
max-3-options constraint has to be restored — it is the only row that ships
four options, and shifting the day's clock is a smaller behavioural change
than the other four. This is that clause being exercised, not a new decision.

### 4b. `ShabbatChips.tsx`

Copy `MealChips.tsx`'s structure exactly — same `role="radiogroup"`, same
class strings, same disabled handling. Two options: `שומר שבת` /
`לא שומר שבת`. `aria-label="שמירת שבת"`.

- `web/src/types.ts`: `shabbat_observant: boolean` on both
  `CreateItineraryRequest` and `Itinerary`.
- `web/src/api/client.ts`: send it.
- `web/src/App.tsx`: `const [shabbatObservant, setShabbatObservant] = useState(true);`
  plus a `handleShabbatSelect` matching the other handlers — they all re-plan
  on select, so follow that; do not add a submit button.

### 4c. Reorder: itinerary first, controls below

Move the whole controls `<div>` in `App.tsx` to **after** the itinerary /
fallback / notices block, so the first thing on screen — and the first thing a
screen reader reaches — is a real day, not a form. The app already plans on
mount (`useEffect`, US-1), so this costs zero taps and no new state.

Give the panel a heading so its new position reads as intentional:
`רוצים משהו אחר?`

Five rows, in this order, top to bottom — strongest effect first, since a
panel below a result is scanned downward:

| # | label | component |
|---|---|---|
| 1 | אזור | `RegionChips` |
| 2 | זמן נסיעה מקסימלי בין עצירות | `DriveTimeChips` |
| 3 | ארוחה | `MealChips` |
| 4 | אורך היום | `DayLengthChips` |
| 5 | שמירת שבת | `ShabbatChips` |

Shabbat is last because it is inert on five days of seven. **Do not reorder it
upward on Fridays** — a panel whose layout changes by weekday is exactly the
kind of surprise this product's accessibility rules exist to prevent.

Row 5 gets a note when it is inert, and only then:

```jsx
<div>
  <p className="mb-2 text-sm font-medium text-slate-600">שמירת שבת</p>
  <ShabbatChips selected={shabbatObservant} onSelect={handleShabbatSelect} disabled={busy} />
  {getTodayWeekday() !== "fri" && (
    <p className="mt-1 text-xs text-slate-500">משפיע על תכנון ליום שישי בלבד</p>
  )}
</div>
```

The row is always rendered. Never conditionally hidden: the accessibility
rules forbid hidden controls ("hidden things do not exist"), and a reviewer
opening the demo on a Tuesday would otherwise never learn the feature exists.
An inert control that says it is inert is honest; one that vanishes is not.

### 4d. Explain the Friday shortfall where it happens

`App.tsx` already computes `dayLengthNotMatched`. When
`shabbatObservant && getTodayWeekday() === "fri"` and that flag is set, the
existing notice should give the real cause:
`היום מסתיים ב-15:00 לקראת שבת, ולכן קצר מהמבוקש`.
Reuse the existing notice element; do not add a second one, and do not put
this text next to the chips — it belongs beside the itinerary it describes.

---

## SPEC amendment

Add **section 17** to `SPEC.md`, in the existing amendment style:

- rule 12's Friday half restated as a deadline on the visit rather than a
  filter on closing time — presented as a bug correction, with the
  before/after availability numbers from Part 2
- `shabbat_observant` on `CreateItineraryRequest` and `Itinerary`, with the
  default-`true` reasoning
- the Saturday non-decision, in full
- the `day_length` interaction on Friday
- start time cut from the UI while `starts_at` stays in the contract —
  recorded as section 2's own clause being exercised, and as a deliberate
  reversal of section 13's control, not a silent deletion
- the controls panel moving below the itinerary, and why: the app plans on
  mount, so a result-first layout costs nothing and puts the answer before
  the form

Then correct **section 2** in place: five chip rows ship, each inside the
three-option cap. The paragraph about start time breaking that cap becomes a
past-tense record of a control that was added, measured against the
constraint, and removed. Keep that history — the fact that it was cut rather
than re-justified is the most interesting thing in the section.

---

## Definition of done

- `python scripts/verify_shabbat_mode.py` exits 0.
- The full suite passes, including the new tests. No existing test assertion
  is weakened or deleted to make something pass — if an old test now
  contradicts the corrected Friday rule, rewrite it deliberately and say so in
  the commit message.
- `SPEC.md` has section 17; section 2 describes five rows.

Seven commits, in this order:

1. `build_matrix.py` reads the region directory + tests
2. regenerated `distance_matrix.json` (data only)
3. Friday deadline fix + tests
4. `shabbat_observant` through the backend + tests
5. frontend: remove `StartTimeChips`
6. frontend: `ShabbatChips`, panel reorder, Friday notice
7. `SPEC.md` section 17 + section 2 correction

## Out of scope

`planner/llm.py`, free-text input, candle-lighting times or any real calendar,
a weekday picker, holiday closures, a multi-screen wizard or summary screen,
deploy config, and any change to the seed data.
