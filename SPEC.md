# MVP Spec — Israeli Day Trip Planner

**Status:** frozen for MVP. Changes require deleting something else.
**Scope:** single-day trips, three regions, curated data, no login, no origin.

---

## 1. User stories

**US-1 — Zero input start**
As a user who opens the app with no idea what I want, I see three regions and
three drive-time options, with defaults already selected. One tap produces a
full itinerary. I never see an empty screen and I never have to type.

*Done when:* first paint shows region chips + drive-time chips with defaults,
and one tap produces a rendered itinerary.

**US-2 — Reshape the day**
As a user looking at a generated itinerary, I can remove a stop with a visible
X, or swap it for an alternative with a visible "swap" button. Every change
recalculates travel time and the end time of the day, shown immediately. Any
removal can be undone from a toast.

*Done when:* remove + swap + undo all work without a confirmation dialog, and
`ends_at` updates within the same render.

**US-3 — Come back later**
As a user who closed the tab mid-planning, I return to the same URL and my
itinerary is exactly as I left it, without logging in.

*Done when:* session UUID in localStorage resolves to a saved itinerary on the
server, restored on load.

---

## 2. The user controls

The MVP shipped with two rows of three chips and nothing else. It now ships
five: region, max drive per leg, meal preference, day length and shabbat
observance (meal preference is a section 10-era addition; day length is
section 13's; shabbat observance is section 17's). This section describes
what actually ships today. Every row here stays inside the original
max-3-options-per-row constraint — the one row that did not, start time, was
cut; its history is recorded at the end of this section rather than quietly
dropped from it.

**Region** — a hard boundary. Every stop in an itinerary belongs to the
selected region. Cross-region itineraries do not exist.

| id        | Hebrew label | covers                                          |
|-----------|--------------|-------------------------------------------------|
| `north`   | צפון         | גליל, גולן, כרמל, עמקים                         |
| `central` | מרכז         | שרון, גוש דן, שפלה, ירושלים והרי יהודה          |
| `south`   | דרום         | נגב, ים המלח, ערבה, אילת                        |

**Max drive per leg** — a cap on travel between two consecutive stops.
Chips: 20 / 45 / 90 minutes. Default 45. No "unlimited" option.

There is **no total-travel setting**. Total driving is bounded automatically by
the maximum day length (rule 10). One knob, not two.

*Why the cap is per leg:* it is the constraint the user actually holds in their
head ("I don't want a long drive between stops"), and it makes distant clusters
self-contained without special-casing them. Eilat needs no exclusion rule: its
attractions sit within ~30 minutes of each other, so a 45-minute cap keeps an
Eilat day in Eilat, and the Eilat↔Dead Sea leg is rejected on its own merits.

**Meal preference** (section 10-era addition, undocumented until now) —
with/without a meal stop. Chips: עם ארוחה / בלי ארוחה. Default with. A
preference, not a hard constraint (rule 4 still caps the itinerary at one
meal stop, positioned 12:00-15:00): `with_meal: false` excludes meal places
from the search entirely, `true` tries to include one before falling back to
a meal-free day, and the response's `meal_included` says which happened.

**Day length** — a preference for how long the day runs. Chips: יום קצר /
יום ארוך (short / long). Default long. Targets a narrower band inside rule
10's hard 4-9h bound (section 13); `length_matched` in the response says
whether the returned day actually landed inside it.

**Shabbat observance** (section 17) — which of two rule-12 Friday behaviours
applies. Chips: שומר שבת / לא שומר שבת. Default שומר שבת (`true`): every
visit ends by 15:00. `false` treats Friday as an ordinary weekday. Inert on
every other day of the week; see section 17 for the reasoning, and for why
Saturday is untouched by this row regardless of which chip is selected.

**History: a sixth row that was added, measured, and cut.** For a period
this section documented a sixth control, start time (08:00 / 09:00 / 10:00 /
11:00, default 09:00, rule 8 amended section 13), sitting alongside these
five. It was the only row ever shipped with four options rather than three,
which broke the original accessibility constraint — at most three chip rows,
at most three options per row — on both counts at once: six rows, not five,
and one of them over the per-row cap. That constraint's own text named start
time as what to cut first if it ever had to be restored rather than
re-justified, precisely because shifting the day's clock is a smaller
behavioural change than removing any of the other five:
`web/src/components/StartTimeChips.tsx` is deleted, `startsAt` state and its
chip are gone from `web/src/App.tsx`, and the constraint holds again — five
rows, each inside the three-option cap. The capability start time added is
not gone with it:
`starts_at` stays a `CreateItineraryRequest` field, `ItineraryPlanner.plan`
still takes it, and the server still defaults it to `"09:00"` — cutting the
control cost nothing on the contract, and `planner/llm.py` will be able to
infer a start time from free text later without touching it. The fact that
this row was cut rather than re-justified, once measured against the
constraint it broke, is the more interesting record than the row itself ever
was.

---

## 3. Definition of a valid itinerary

An `Itinerary` is **valid** only if all of the following hold. The planner must
reject and retry rather than emit an invalid itinerary; the API must never
return one.

**Content**
1. Every `stop.place_id` resolves to a place in the seed dataset. No invented
   places, names, or opening hours.
2. Between 3 and 6 stops. Fewer is not a day; more violates cognitive load.
3. No place appears twice.
4. At most one stop with `category = "meal"`, positioned between 12:00 and
   15:00.

**Geography**
5. Every place has `region == itinerary.region`. No exceptions, no "just over
   the line".
6. For every consecutive pair, `travel_min_from_prev <= max_leg_min`.
7. Travel times come from the precomputed matrix. Never estimated by a model.

**Time**
8. `starts_at` (amended, section 13) is a request field — 08:00, 09:00, 10:00
   or 11:00, default 09:00 — and the first stop arrives exactly at it. There
   is no origin and no travel leg before the first stop. `ends_at` is always
   computed server-side; the user never picks it.
9. Times are strictly increasing:
   `arrive_at[i] + duration[i] + travel_min[i+1] == arrive_at[i+1]`.
10. Total elapsed time (`ends_at - starts_at`) is between 4 and 9 hours — the
    hard bound, never relaxed. `day_length` (amended, section 13) targets a
    narrower band inside it as a preference, not a further constraint.
11. Every stop is open at `arrive_at` on the requested weekday, per the seed
    `opening_hours`. A place closed that day is never scheduled.

**Shabbat**
12. If the requested day is Saturday, no stop with `closed_on_shabbat = true`.
    If Friday, every stop must close before 15:00 in its own hours.

**Language**
13. Every user-facing string is Hebrew (`name_he`, `description_he`,
    `tip_he`). Every key, id, enum value and internal field is English.

---

## 4. Sparse-result fallback

Region + a tight leg cap can yield too few places to satisfy rules 2 and 6.
An empty screen is forbidden, so the API degrades in this fixed order:

1. Relax `max_leg_min` one step (20→45→90). Return the itinerary with
   `relaxed_to: 45` set, and the client shows a toast:
   *"הרחבנו את זמן הנסיעה ל-45 דקות כדי למצוא מספיק מקומות"*.
2. If 90 minutes still fails, return `days: []` plus a non-empty `places[]`
   of everything found in the region. The client renders them as cards with no
   schedule and a single line explaining why.

Never a dialog, never a blank state, never a silent failure.

---

## 5. Out of scope for MVP

Explicitly not built. Not "later in the sprint" — not in this deliverable.

- **Multi-day / weekend trips.** `days: Day[]` is kept so the door stays open,
  but MVP always emits exactly one day.
- **Cross-region itineraries.**
- **Origin, geolocation, "start from my location", travel time from home.**
- **Accommodation, hotels, lodging.**
- **User accounts, login, auth, email.** Session UUID only.
- **pgvector / semantic search.** On ~100 curated places, tag filtering beats
  embeddings and is explainable in an interview.
- **Live routing APIs** (Google Directions, Waze). Precomputed matrix only.
- **Real-time data:** live hours, weather, traffic, prices, crowds.
- **Booking, payments, tickets.**
- **Maps as an interaction surface.** Static pins acceptable if time allows;
  pan/zoom/route-drawing is not.
- **Sharing, PDF export, calendar integration, print styles.**
- **Mobile-native app.** Responsive web only.
- **i18n / English UI.** Hebrew RTL, one locale.
- **Admin panel.** Places are edited by editing the seed file.
- **Analytics, A/B testing, feature flags.**
- **Free-text entry for region or drive time.** Chips only.

---

## 6. Data plan

The original plan was ~30–35 places per region, ~100 total, seeded by an
OSM/Overpass acquisition pipeline (`api/scripts/fetch_osm.py`,
`scrape_parks.py`, `curate_osm_open.py`) into `api/data/candidates.json` /
`seed_names.json` for later review. That pipeline is no longer the
acquisition path. It pulled on the order of a thousand auto-collected,
unverified candidates — far more rows than anyone could actually confirm
opening hours for — and the dataset shipped today is what survived hand
verification instead: **37 places** across three regions (13 north, 17
central, 7 south as of this writing), each with `hours_verified` checked
against an official source and, where one exists, an `official_url`.

**The decision, stated plainly:** a small, hand-verified dataset beats a
large, unverified one. Rule 1's "never fill a gap from memory" only means
something if every row it protects was actually checked by a person; a
thousand auto-collected rows nobody has verified are not a bigger asset than
37 rows that are all real, they are a thousand latent bugs. `hours_verified`
exists precisely so a `gated` place with unverified hours can be told apart
from a checked one — section 10 makes the former unschedulable (better "not
shown" than "shown with invented hours"). The current dataset has zero such
rows, so this filter changes nothing today; it exists to block the day one
does appear.

Every place still carries `region` as a stored field, assigned by hand — never
derived from coordinates at runtime (`domain/regions.latitude_looks_wrong`
only smoke-tests that assignment, it never overrides it). The scraping
scripts remain in the repo, but only as one-off migration tooling run
against the already-curated set (e.g. `backfill_official_url.py`,
`clamp_overnight_hours.py`, section 14) — not as a recurring source of new,
unverified rows.

---

## 7. API contract (freeze before splitting work)

### Shared types

```ts
type PlaceId = string;              // "jer-israel-museum"
type Region = "north" | "central" | "south";
type MaxLegMin = 20 | 45 | 90;
type Category = "museum" | "nature" | "hike" | "viewpoint"
              | "meal" | "kids" | "historic";

interface Place {
  id: PlaceId;
  name_he: string;
  description_he: string;
  tip_he: string;
  description_source: "generated" | "human"; // provenance of description_he/tip_he
                                    // only — not a claim about hours (section 10)
  category: Category;
  region: Region;                  // stored, not computed
  access: "gated" | "open";        // "gated": a real gate/ticket/staff — opening_hours
                                    // applies. "open": free-access trail, spring,
                                    // viewpoint — opening_hours is always null, and
                                    // the place is scheduled by a fixed daylight
                                    // window instead (section 10; DAYLIGHT_START/
                                    // DAYLIGHT_END in domain/schedule.py)
  lat: number | null;              // null = not yet resolved; never schedulable
  lng: number | null;
  duration_min: number;
  opening_hours: Record<Weekday, [string, string] | null>; // null = closed;
                                    // always seven nulls when access == "open"
  hours_verified: boolean;         // true only when a human read official hours
                                    // (section 10)
  closed_on_shabbat: boolean;
  kid_friendly: boolean;
  accessible: boolean;
  tags: string[];                  // English, lowercase; from places.template.json's
                                    // _tag_vocabulary
  season: "year_round" | "summer_only"; // default "year_round"; summer_only is
                                    // excluded outside April-October (section 11;
                                    // SUMMER_ONLY_MONTHS in domain/schedule.py)
  images: PlaceImage[];            // up to 3, Commons-sourced; most places have none
  official_url: string | null;     // the place's own page; null renders no link,
                                    // never a dead one
}

interface PlaceImage {
  url: string;                     // local path under /images/, never hotlinked
  credit: string;                  // e.g. "Hoshvilim, CC BY-SA 4.0"
  source_url: string;              // the Commons file page, not the raw upload URL
}

interface Stop {
  place_id: PlaceId;
  place: Place;
  arrive_at: string;               // "09:00"
  duration_min: number;
  travel_min_from_prev: number;    // 0 for first stop
}

interface Day {
  stops: Stop[];
  starts_at: string;
  ends_at: string;                 // computed server-side, never trusted
                                   // from the client
}

interface Itinerary {
  id: string;                      // session-scoped UUID
  region: Region;
  max_leg_min: MaxLegMin;
  weekday: Weekday;
  days: Day[];                     // MVP: length 1, or 0 on fallback step 2
  places: Place[];                 // populated only on fallback step 2
  relaxed_to: MaxLegMin | null;    // set when fallback step 1 fired
}
```

### Endpoints

```
POST /api/itinerary          -> Itinerary
  body: { session_id, region, max_leg_min, weekday, prompt_he? , chip? }

GET  /api/itinerary/{id}     -> Itinerary          # US-3 resume

POST /api/itinerary/{id}/remove   -> Itinerary     # body: { place_id }
POST /api/itinerary/{id}/swap     -> Itinerary     # body: { place_id }
POST /api/itinerary/{id}/undo     -> Itinerary

GET  /api/places?region=     -> Place[]            # lets the web side build
                                                   # UI before the planner
                                                   # exists
```

Every mutating endpoint returns the **full recomputed Itinerary**. The client
never does time arithmetic. This is the most important line in the contract:
the frontend becomes a pure renderer and all time logic lives in one testable
module.

---

## 8. File tree

```
/api
  main.py                    # FastAPI app, routes only
  models.py                  # Pydantic mirrors of the types above
  planner/
    base.py                  # ItineraryPlanner interface
    rule_based.py            # MVP implementation + permanent fallback
    llm.py                   # later. same interface.
  domain/
    regions.py               # region constants + membership
    shabbat.py               # + test_shabbat.py
    schedule.py              # time arithmetic + validation (rules 1-13)
    distance.py              # matrix loading / lookup
  repository/
    places.py                # PlaceRepository, JSON-backed for now
  data/
    places/                  # one *.json file per region, not one shared file
      north.json
      central.json
      south.json
    distance_matrix.json     # generated
  scripts/
    build_matrix.py

/web
  src/
    api/client.ts
    types.ts                 # hand-mirrored from the contract
    components/
      RegionChips.tsx
      DriveTimeChips.tsx
      StopCard.tsx
      Timeline.tsx
      Toast.tsx
    App.tsx
```

**Ownership split:** one owns `/api`, the other owns `/web`. The contract above
is the only shared surface. `GET /api/places` exists so the web side can render
real cards on day one without waiting for the planner.

---

## 9. Honest risk note

The hard part of this project is not the LLM. It is producing ~100 places with
correct, verified opening hours across three regions. Budget more time for it
than feels reasonable and start before the planner is finished — it is the only
task here that cannot be compressed by writing better code.

---

## 10. Amendment: OSM-assisted seeding (2026-08-30)

`Place` (section 7) gains three fields, added to support `BRIEF_data_pipeline.md`'s
OSM ingest + LLM-description pipeline:

```ts
interface Place {
  // ...as above...
  access: "gated" | "open";        // "gated": a real gate/ticket/staff — opening_hours
                                    // applies. "open": free-access trail, spring,
                                    // viewpoint — opening_hours is always null.
  hours_verified: boolean;         // true only when a human read official hours
  description_source: "generated" | "human";
}
```

Scheduler consequence: an `open` place is scheduled by daylight
(07:00–18:00) rather than by `opening_hours` — implemented in
`domain/schedule.py` (`DAYLIGHT_START`/`DAYLIGHT_END`, used by `is_open_at`
and `visit_fits_opening_hours`). Rule 11 does not change; this only defines
what counts as "open" for a place OSM ingest could not verify.

The other half — a `gated` place with `hours_verified == false` must never
be scheduled — is implemented in the same function, `is_available_on`,
checked unconditionally before the weekday/season/shabbat checks so it is
enforced in `PlaceRepository.candidates()` before the planner's search ever
runs (section 15).

This is the only exception to the freeze note at the top of this file — the
fields are additive and no existing rule changes.

---

## 11. Amendment: seasonal places (2026-08-30)

`Place` (section 7) gains one more field, added to support
`BRIEF_data_acquisition.md`'s multi-source data acquisition (parks.org.il,
OSM open-access curation, data.gov.il museums, hand-typed):

```ts
interface Place {
  // ...as above...
  season: "year_round" | "summer_only";   // default "year_round"
}
```

Scheduler consequence: a `summer_only` place must never be scheduled outside
April–October — implemented in `domain/schedule.py`'s `is_available_on`,
gated by `SUMMER_ONLY_MONTHS` (April–October inclusive) against the month the
request resolves to. Additive field, no existing rule changes.

---

## 12. Amendment: one seed file per region (2026-09-01)

`api/data/places.json` is split into `api/data/places/north.json`,
`central.json` and `south.json` — same `{"places": [...]}` shape as before,
partitioned by each place's `region` field. Two people editing different
regions no longer collide on one shared file.

`PlaceRepository.load()` (section 8) reads every `*.json` file in the
directory (`$PLACES_DIR`, default `api/data/places/`) and concatenates them
in filename order. Two checks fail loudly at startup rather than passing a
bad seed through silently:

- an entry whose `region` doesn't match the file it's in
- the same `(region, name_he)` appearing in two different files — a real
  duplicate to resolve by hand, never silently deduped

`id` and `duration_min` are still read straight from each row, unchanged
from before. No change to the `Place` schema itself (section 7) or to any
rule 1-13.

---

## 13. Amendment: request-controlled start time and day length (2026-09-01)

`CreateItineraryRequest` (section 7) gains two fields:

```ts
interface CreateItineraryRequest {
  // ...as above...
  starts_at?: "08:00" | "09:00" | "10:00" | "11:00";  // default "09:00"
  day_length?: "short" | "long";                       // default "long"
}
```

`starts_at` is rule 8's start time, now chosen per request instead of fixed.
`ends_at` is unaffected — still always computed server-side.

`day_length` is a **preference** for a band inside rule 10's hard 4-9h
bound, never a further hard constraint:

| value   | target band |
|---------|--------------|
| `short` | 4.0-5.5h     |
| `long`  | 6.5-9.0h     |

`Itinerary` gains a matching response field:

```ts
interface Itinerary {
  // ...as above...
  length_matched: boolean;
}
```

`length_matched` is `true` only when the returned day's elapsed time actually
landed inside the requested band. A late `starts_at` combined with `"long"`
will often be impossible once opening hours bite — the planner returns the
closest valid day inside the hard bound instead and sets `length_matched:
false`, the same "preference degrades, never fails" shape as `with_meal` /
`meal_included` (section 10-era addition, undocumented until now — both
follow the identical pattern: try to satisfy the preference across the full
`max_leg_min` relaxation ladder; if nothing lands, retry without the
preference; only fail to fallback step 2 if that also finds nothing).

Opening hours already respect whichever `starts_at` was requested: a gated
place that opens at 09:00 is filtered out of the candidate pool for an 08:00
start the same way it always was for a place closed all day — no separate
rule, just rule 11 applied at the requested clock instead of a fixed one.

---

## 14. Amendment: opening_hours cannot cross midnight (2026-09-02)

`opening_hours` (section 7) is a same-day `[open, close]` pair and stays
that way: it cannot express a window that runs past midnight (e.g. a bar
open 12:00-01:00). A place scraped with hours like that is stored with its
close time clamped to `23:59` instead — `12:00-01:00` becomes `12:00-23:59`
— via `api/scripts/clamp_overnight_hours.py`, never by hand-editing the
seed.

This is acceptable, not just a stopgap: the planner only schedules daytime
visits, and rule 10's hard 4-9h day-length bound means a day that starts at
`starts_at` (rule 8, section 13) is always over well before midnight. The
post-midnight tail of a `12:00-01:00` window is consequently never
schedulable anyway — clamping it away loses nothing rule 1-13 could ever
have used.

Revisit this if the product ever adds nightlife (late-evening stops, a day
that can start after dark) — at that point `opening_hours` needs a real
overnight representation, not a clamp.

---

## 15. Amendment: documentation sync (2026-09-02)

No behavior changed; this SPEC had drifted from the code it describes.
Corrected in place rather than as a new layer:

- Section 2 rewritten for the five controls actually shipped (region, drive
  time, meal, start time, day length), including the acknowledged tension
  with the max-3-options-per-row constraint that start time now breaks.
- Section 6 rewritten: the ~100-place OSM-pipeline plan was abandoned for a
  small hand-verified dataset (37 places, three regions) — recorded as a
  decision, not silently updated as a number.
- Section 7's `Place` interface now includes every field the later
  amendments (sections 10, 11) and two previously-undocumented additions
  (`images`, `official_url`) actually added, so it is readable on its own.
  The amendments themselves are left as the change log, unchanged.
- Sections 10 and 11's "not yet implemented" scheduler-consequence notes
  replaced with pointers to where each rule actually lives
  (`domain/schedule.py`'s daylight window and `SUMMER_ONLY_MONTHS`). Section
  10's other consequence — an unverified gated place must never be
  scheduled — was genuinely still unimplemented at the time this sync was
  written, and was called out as such rather than bundled with the part
  that shipped. It was implemented the same day; see section 16.

---

## 16. Amendment: unverified gated places are unschedulable (2026-09-02)

Section 10's other scheduler consequence — a `gated` place with
`hours_verified == false` must never be scheduled — is now implemented,
alongside the daylight rule it was documented next to: `domain/schedule.py`'s
`is_available_on` rejects such a place unconditionally, before the
weekday/season/shabbat checks, so it is enforced in
`PlaceRepository.candidates()` before the planner's search ever runs. An
`open` place is unaffected either way — it has no gate and no hours to
verify, so `hours_verified` says nothing about it.

The current dataset has zero `gated` places with `hours_verified == false`,
so this filter changes nothing today, in any region. It exists to block the
day one does appear rather than let it silently reach a user with invented
hours.

---

## 17. Amendment: Friday is a deadline, `shabbat_observant` (2026-09-05)

### Rule 12's Friday half was a bug, corrected

Rule 12 (section 3) said: *"If Friday, every stop must close before 15:00 in
its own hours."* That was implemented as a filter on the place's own closing
time, and it was wrong twice over:

1. An `access == "open"` place has seven `null` opening hours **by design**
   (section 10) — there is no gate for a weekly schedule to describe. The old
   code read that `null` as "closed on Friday" and dropped every such place,
   every Friday.
2. Even for a `gated` place, "closes by 15:00" is stricter than the rule
   ever needed. A museum open 09:00–18:00 on Friday can be visited
   09:00–11:00 with the user home long before Shabbat. The rule the product
   actually wants is *the visit ends by 15:00*, not *the place shuts by 15:00*.

Rule 12's Friday half is now: **every visit must end by 15:00; the day's
`ends_at` must be `<= 15:00`.** This is enforced as a clamp on the effective
opening window (`domain/schedule.py`'s `_effective_window`), not as a
rejection of the place — a `gated` place open 16:00–20:00 on Friday still has
no room after the clamp and is correctly unschedulable, with no special case
needed. Measured on the seed, places available on Friday:

| region  | before | after |
|---------|--------|-------|
| north   | 2      | 12    |
| central | 3      | 15    |
| south   | 0      | 5     |

Saturday's rule — a `closed_on_shabbat` place is never scheduled, regardless
of what `opening_hours["sat"]` says — is unchanged and stays unconditional
for every user; see the non-decision below for why it stays that way even
after `shabbat_observant` is introduced.

### `shabbat_observant`: a planner input, default `true`

`CreateItineraryRequest` and `Itinerary` (section 7) both gain:

```ts
interface CreateItineraryRequest {
  // ...as above...
  shabbat_observant?: boolean;   // default true
}

interface Itinerary {
  // ...as above...
  shabbat_observant: boolean;
}
```

This is a **planner input**, not an LLM feature: a boolean that selects which
of two documented rule-12 Friday behaviours applies. There is no
`planner/llm.py` in this repo yet; when it lands, its job will be to map free
Hebrew text ("אנחנו שומרי שבת") onto this field, exactly as it will for
`region` and `with_meal`. Adding the field now is what makes that later
mapping a one-line change instead of a new rule.

|         | `shabbat_observant: true` (default) | `false`                    |
|---------|--------------------------------------|----------------------------|
| Fri     | every visit ends by 15:00; day `ends_at <= 15:00` | ordinary weekday; real hours / daylight apply |
| Sat     | `closed_on_shabbat` places excluded | identical — same exclusion |
| Sun–Thu | no effect                           | no effect                  |

It is persisted on `Itinerary`, not read only from the request, for the same
reason `max_leg_min` and `weekday` are: `remove` / `swap` / `undo` recompute
a stored itinerary and have no request of their own, and an edit must not
silently change the rule the day was built under.

**Default is `true`.** The two failure modes are not symmetric. Defaulting
to `false` and being wrong schedules an observant user into Shabbat;
defaulting to `true` and being wrong only gives a secular user a Friday that
ends earlier than it had to — recoverable with one tap. Defaulting `true`
also preserves the corrected rule 12 behaviour above as the out-of-the-box
experience, so this amendment is additive rather than a second behaviour
change riding on the first.

**Saturday is deliberately untouched — a considered non-decision.** Whether
an observant user would travel on Shabbat at all is not something this app
can answer: there is no origin, no location, and no way to build a
walking-distance day (section 5). The factual filter this app *does* have —
a place closed on Shabbat is closed for everyone, `closed_on_shabbat` —
already covers what the seed data actually knows, and stays unconditional
regardless of `shabbat_observant`. Making Saturday's exclusion conditional
on the flag would imply the app knows something about an observant user's
Saturday travel that it does not; leaving it alone says exactly what the app
can and cannot answer. This is written down rather than left implicit
because an undocumented non-decision reads, on the next pass, as an
oversight to "fix."

**Interaction with `day_length` (section 13) — expected, not a bug.** Friday
+ `shabbat_observant: true` + the default 09:00 start caps the day at 6.0
hours (09:00–15:00), below the `"long"` band's 6.5h floor. `length_matched`
is therefore `false` for every such request. This is section 13's existing
"preference degrades, never fails" shape working exactly as designed — rule
10's hard 4–9h bound is still satisfied, the day is still valid, and nobody
should "fix" the mismatch by relaxing rule 10 or the deadline.

**Known limitation, surfaced while testing this amendment: `day_length:
"long"` can occasionally fail to find a day that exists.** Writing the test
above (a large `shabbat_observant: false` Friday pool, `day_length: "long"`)
uncovered a pre-existing bug in `planner/rule_based.py`'s search, not
something this amendment introduced or is responsible for fixing. `"short"`'s
target band has an upper bound well inside rule 10's hard 9h ceiling, so
`_extend`'s early prune (elapsed already past the band's own top) kicks in
well before the hard bound would anyway. `"long"`'s upper bound *is* the hard
9h ceiling, so that same prune buys nothing extra for it: a large pool of
same-duration candidates can occasionally have its `PREFERRED_STOP_COUNTS`
order shuffled so that the impossible 6-stop case is tried before the 4- or
5-stop case that would actually succeed, and fully exploring every 6-stop
combination first can exhaust the whole search budget — occasionally
reporting no valid day when a "long" one plainly exists. Measured at roughly
1 run in 20–50 with a 10-candidate pool. Out of scope for this amendment
(`planner/rule_based.py`'s search algorithm is explicitly not touched here);
`api/planner/test_rule_based.py`'s
`test_long_day_length_with_ten_uniform_candidates_sometimes_fails_to_find_a_day`
is marked `xfail(strict=False)` to document it without either hiding it or
failing the suite on the runs that hit it.

### Frontend: five rows, panel below the itinerary

Three changes, all in `/web`, recorded together because the third is what
the first two made room for:

- **Start time cut, capability kept** — see section 2's own history
  paragraph for the row's full record. In short: `StartTimeChips.tsx` and its
  state are deleted from `/web`, but `starts_at` stays in
  `CreateItineraryRequest`, `ItineraryPlanner.plan`, and `types.ts`, still
  defaulting to `"09:00"` server-side. This is section 2's own
  max-3-options-per-row clause being exercised — start time was already
  named as what to cut first — not a silent deletion, and it deliberately
  reverses section 13's addition of a start-time control rather than
  building on it.
- **`ShabbatChips.tsx`** — the sixth row's actual UI, added in start time's
  place: two options, `שומר שבת` / `לא שומר שבת`, structured identically to
  `MealChips.tsx`.
- **The controls panel moves below the itinerary.** `App.tsx` already plans
  on mount (`useEffect`, US-1), so a result-first layout — itinerary,
  fallback, and notices first, the five-row panel (headed `רוצים משהו אחר?`)
  after — costs zero taps and no new state, and puts the answer before the
  form for every visitor, sighted or not. Shabbat observance is deliberately
  the last of the five rows and never reorders itself on Friday: a panel
  whose layout changes by weekday would be exactly the kind of surprise this
  product's accessibility rules exist to prevent. The row is always
  rendered, even on the five days it is inert, with a small note saying so —
  a hidden control is invisible to a reviewer who opens the demo on a
  Tuesday, and an inert control that says it is inert is honest in a way a
  vanishing one is not.
