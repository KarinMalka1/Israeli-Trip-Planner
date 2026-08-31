# Implementation brief — seed data pipeline

Hand this to Claude Code as-is. It describes two standalone scripts. Do not
touch `/web`, the planner, or the API while implementing these.

Read `SPEC.md` first — sections 3 (valid itinerary), 6 (data plan) and 7 (API
contract) define the schema these scripts must emit.

---

## Goal

Produce `api/data/places.json` — ~100 real Israeli attractions in the `Place`
schema — with as little hand-typing as possible, without inventing any fact.

Two scripts, run in order:

```
scripts/fetch_osm.py            # facts, from OpenStreetMap
scripts/generate_descriptions.py  # Hebrew prose, from an LLM, offline
```

Both are offline batch tools. Neither is ever imported by the running API.

---

## Hard rules

1. **Never invent a fact.** Names, coordinates, opening hours and accessibility
   come from OSM or are left empty. If a field is unknown, emit `null` and set
   the corresponding `_verified` flag to `false`. Do not guess.
2. **The LLM writes prose only.** It receives factual fields and returns two
   Hebrew sentences. It never produces a name, an id, coordinates, hours, or a
   category. If a generated description contains a number that looks like an
   opening time or a price, discard it and retry.
3. **Both scripts are idempotent.** Re-running must not duplicate entries or
   overwrite human edits (see "Merge behaviour").
4. **English everywhere** except `name_he`, `description_he`, `tip_he`.
5. No new runtime dependencies for the API. These scripts may use `requests`
   and `anthropic`; the FastAPI app must not.

---

## Schema changes to make first

`SPEC.md` section 7 defines `Place`. Add three fields to `models.py` and to the
TypeScript contract, and note the change at the bottom of `SPEC.md`:

```python
access: Literal["gated", "open"]
# "gated"  = has a gate/ticket/staff, real opening_hours apply
# "open"   = free-access trail, spring, viewpoint. opening_hours is null and
#            the scheduler treats it as daylight-only.

hours_verified: bool          # True only when a human read official hours
description_source: Literal["generated", "human"]
```

Scheduler consequence, to implement later in `domain/schedule.py` — state it in
a TODO comment now:

- `access == "gated"` and `hours_verified == False` → **never scheduled**.
- `access == "open"` → schedulable, clamped to 07:00–18:00.

This is what lets you ship with auto-filled data without lying to users.

---

## Script 1 — `scripts/fetch_osm.py`

### Behaviour

```
python scripts/fetch_osm.py --region north --limit 40
python scripts/fetch_osm.py --all
```

Queries the Overpass API per region bounding box, maps OSM tags to the `Place`
schema, writes `api/data/places.json`.

### Region bounding boxes

Coarse, deliberately. They are query filters only — the `region` field written
to each place is the region whose query returned it.

| region  | south | west | north | east |
|---------|-------|------|-------|------|
| north   | 32.60 | 34.90 | 33.35 | 35.90 |
| central | 31.40 | 34.50 | 32.60 | 35.55 |
| south   | 29.45 | 34.20 | 31.40 | 35.50 |

Boxes must not overlap: a place returned by two queries keeps the first and is
logged as a duplicate.

### Overpass query

Endpoint: `https://overpass-api.de/api/interpreter`.
Send a descriptive `User-Agent` header identifying the project.
Cache the raw response to `api/data/.cache/overpass_{region}.json` and reuse it
if it exists unless `--refresh` is passed. Overpass is rate-limited and slow;
never hit it twice for the same data during development.

Query nodes, ways and relations with these tags inside the bbox:

```
tourism=museum
tourism=attraction
tourism=viewpoint
tourism=zoo
tourism=aquarium
historic=archaeological_site
historic=ruins
historic=castle
leisure=park
leisure=nature_reserve
natural=spring
natural=cave_entrance
natural=beach
waterway=waterfall
```

Use `out center;` so ways and relations return a representative coordinate.

### Filtering — drop an element if

- it has no `name:he` **and** no `name` tag containing Hebrew characters
- it has no usable coordinate
- its name is under 3 characters
- it duplicates an already-accepted place: same normalised name, or within
  150 m of an accepted place with a similar name

Log every drop with the reason to stderr. Print a summary table at the end:
per region, how many were fetched, dropped, accepted, and how many have real
opening hours.

### Field mapping

| Place field | source |
|---|---|
| `id` | `{region}-{slug(name_he)}`, ASCII, deduped with `-2` suffix |
| `name_he` | `name:he`, else `name` if Hebrew |
| `lat` / `lng` | node coords, or `center` for ways/relations |
| `category` | tag map below |
| `region` | the region whose query returned it |
| `duration_min` | default per category (below) |
| `opening_hours` | parse OSM `opening_hours` if present, else `null` |
| `hours_verified` | `True` only if OSM `opening_hours` parsed cleanly |
| `access` | `"gated"` if `fee=yes` or `tourism` in (museum, zoo, aquarium) or `leisure=nature_reserve`, else `"open"` |
| `closed_on_shabbat` | `False` unless OSM hours say closed Sa; needs human review either way |
| `kid_friendly` | `True` for park, zoo, aquarium, beach, spring; else `False` |
| `accessible` | `True` if `wheelchair=yes`, `False` if `no`/`limited`, `False` if absent |
| `tags` | derived from OSM tags, lowercase English, from the vocabulary in `places.template.json` |
| `description_he` / `tip_he` | `""` — filled by script 2 |
| `description_source` | `"generated"` |
| `_osm_id`, `_osm_type` | keep, for re-fetching later |

**Category map:**

```
museum, zoo, aquarium                    -> museum
nature_reserve, park, spring, waterfall,
  beach, cave_entrance                   -> nature
viewpoint                                -> viewpoint
archaeological_site, ruins, castle       -> historic
attraction                               -> kids if name/tags suggest a
                                            playground or family site,
                                            else historic
```

`meal` is never produced by OSM ingest. Restaurants are added by hand later —
one per region is enough for MVP.

**Default `duration_min`:** museum 120, historic 60, nature 90, viewpoint 30,
kids 120, hike 150.

### Opening-hours parsing

OSM `opening_hours` is a small language (`Mo-Fr 08:00-17:00; Sa off`). Do not
write a full parser. Handle only the simple cases:

- a single weekday range with a single time range
- `24/7`
- explicit `off` days

Anything else → `opening_hours = null`, `hours_verified = False`, and log it.
Partial correctness here is worthless; a wrong gate time is the exact failure
the project exists to prevent.

### Merge behaviour

If `places.json` already exists:

- match on `_osm_id`
- **never** overwrite a place whose `hours_verified` is `True` or whose
  `description_source` is `"human"`
- add new places, update coordinates and names on unverified ones
- never delete; log entries that vanished from OSM under a `_stale: true` flag

---

## Script 2 — `scripts/generate_descriptions.py`

### Behaviour

```
python scripts/generate_descriptions.py --limit 20
python scripts/generate_descriptions.py --place-id central-ein-hemed
```

Fills `description_he` and `tip_he` for places where they are empty. Uses the
Anthropic API, model `claude-sonnet-4-6`, key from the `ANTHROPIC_API_KEY`
environment variable. Never commit the key; add `.env` to `.gitignore`.

### Prompt design

System prompt in English. One place per request, batched with a concurrency of
about 4. The model receives only:

```json
{"name_he": "...", "category": "nature", "region": "north",
 "tags": ["spring", "shade"], "access": "open", "duration_min": 90}
```

Ask for exactly this back, and nothing else — no markdown fence, no preamble:

```json
{"description_he": "...", "tip_he": "..."}
```

Constraints to state in the system prompt:

- `description_he`: one or two sentences, present tense, plain Hebrew.
- `tip_he`: one practical sentence.
- Base everything only on the fields supplied.
- Never state opening hours, prices, phone numbers, dates or distances.
- Never claim a place is accessible, safe, or suitable for children unless the
  supplied fields say so.
- If the name alone is not enough to say anything specific, write something
  generic and true rather than inventing detail.

### Validation before writing

Reject and retry once, then skip and log, if the response:

- is not valid JSON with exactly those two keys
- contains a digit followed by `:` (a time), or `₪`, or a 4-digit year
- exceeds 200 characters in either field
- contains Latin characters beyond the occasional acronym

Set `description_source: "generated"` on everything it writes.

---

## Review tooling

Add `scripts/review_places.py` — prints places needing human attention, in
priority order:

1. `access == "gated"` and `hours_verified == False` (these are unschedulable)
2. `description_source == "generated"` and never reviewed
3. missing `tags`

It prints one place per screen with its OSM link and waits for `y`/`n`/`skip`.
On `y` it flips `description_source` to `"human"` or `hours_verified` to
`True`. Keep it under 80 lines; it is a chore tool, not a product.

---

## Tests

`scripts/test_fetch_osm.py`, using saved fixture JSON — never hitting the
network in tests:

- tag mapping produces the expected `category` for one element of each type
- an element with no Hebrew name is dropped
- two elements 50 m apart with the same name produce one place
- `opening_hours` parses `Mo-Fr 08:00-17:00; Sa off` correctly
- `opening_hours` on an exotic string yields `null` and `hours_verified: False`
- merging preserves a place marked `hours_verified: True`

`scripts/test_generate_descriptions.py`, with a stubbed client:

- a response containing a time string is rejected
- a response with extra keys is rejected
- valid output is written and `description_source` set to `"generated"`

---

## Definition of done

- `python scripts/fetch_osm.py --all` produces `places.json` with ≥ 90 places
  spread across the three regions.
- Every entry validates against the Pydantic `Place` model.
- The summary print states how many places are currently schedulable
  (`access == "open"`, or `gated` with `hours_verified == True`).
- `README.md` gains an OpenStreetMap attribution line: data © OpenStreetMap
  contributors, ODbL.
- No API key, `.env`, or `.cache/` directory is committed.

---

## Out of scope for this brief

Do not build: a scheduler, an API endpoint, a UI, a database, an admin panel, a
full OSM `opening_hours` parser, or anything that calls an LLM at request time.
