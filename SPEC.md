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
five: region, max drive per leg, meal preference, start time and day length
(meal preference is a section 10-era addition; start time and day length are
section 13's — see the change log). This section describes what actually
ships today; the tension that growth creates with the original
max-3-options-per-row constraint is addressed at the end of this section
rather than quietly dropped.

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

**Start time** — when the first stop arrives. Chips: 08:00 / 09:00 / 10:00 /
11:00. Default 09:00 (rule 8, amended section 13). `ends_at` is still always
computed server-side; the user never picks it.

**Day length** — a preference for how long the day runs. Chips: יום קצר /
יום ארוך (short / long). Default long. Targets a narrower band inside rule
10's hard 4-9h bound (section 13); `length_matched` in the response says
whether the returned day actually landed inside it.

**Tension with the max-3-options rule.** The original accessibility
constraint — at most three chip rows, at most three options per row — is now
broken on both counts: five rows ship, not two or three, and start time
carries four options, not three (flagged in the component itself,
`web/src/components/StartTimeChips.tsx`, rather than silently resolved).
Region, drive time, meal and day length each earn their place by mapping to
a distinct, otherwise-invisible planner behavior a user would reasonably
want to steer — which stops exist at all, how far apart they can be, whether
one is a meal, and how long the day runs — and each stays inside the
three-option cap. Start time is the odd one out on both counts: it is the
newest addition, it already exceeds three options, and shifting the whole
day's clock is a smaller behavioral change than the other four. If this
constraint has to be restored rather than re-justified, start time is what
we would cut first — collapsing back to the original fixed 09:00 default —
not one of the other four.

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
