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

## 2. The two user controls

The entire input surface is two rows of three chips. Nothing else.

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
the maximum day length (rule 9 below). One knob, not two.

*Why the cap is per leg:* it is the constraint the user actually holds in their
head ("I don't want a long drive between stops"), and it makes distant clusters
self-contained without special-casing them. Eilat needs no exclusion rule: its
attractions sit within ~30 minutes of each other, so a 45-minute cap keeps an
Eilat day in Eilat, and the Eilat↔Dead Sea leg is rejected on its own merits.

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
8. `starts_at` is 09:00 at the first stop. There is no origin and no travel
   leg before the first stop.
9. Times are strictly increasing:
   `arrive_at[i] + duration[i] + travel_min[i+1] == arrive_at[i+1]`.
10. Total elapsed time (`ends_at - starts_at`) is between 4 and 9 hours.
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

~30–35 places per region, ~100 total. Every place carries `region` as a stored
field, assigned by hand in the seed — never derived from coordinates at
runtime.

**Verification rule:** do not add a place whose opening hours you have not read
from an official source (רשות הטבע והגנים, the site's own page, the local
authority). Never fill a gap from memory. This dataset is the product; the
planner is only as good as it is.

Central is verifiable in person from campus. North and south rely on official
sources, which is slower — start them first.

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
  category: Category;
  region: Region;                  // stored, not computed
  lat: number;
  lng: number;
  duration_min: number;
  opening_hours: Record<Weekday, [string, string] | null>; // null = closed
  closed_on_shabbat: boolean;
  kid_friendly: boolean;
  accessible: boolean;
  tags: string[];                  // English, lowercase
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
    places.json
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
