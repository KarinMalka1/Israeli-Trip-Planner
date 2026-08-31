# Implementation brief — data acquisition, all sources

Hand to Claude Code alongside `SPEC.md` and `BRIEF_data_pipeline.md`.

Four phases in strict priority order. All four categories ship in this
session. Only Phases 1 and 2 are scrapers; Phase 3 is a dataset download and
Phase 4 is hand-typed.

**Checkpoint after Phase 2** — show me the numbers before continuing.

Everything here writes to `api/data/seed_names.json`, which is then consumed by
the existing `fetch_osm.py` enrichment pass. Nothing here writes `places.json`
directly.

---

## Rules that apply to every scraper

1. **Read `robots.txt` first.** If it disallows the target paths, stop and
   report rather than proceeding. No exceptions, no user-agent games.
2. **1 request per second**, descriptive `User-Agent` naming the project and
   including a contact address.
3. **Cache every fetched page** to `api/data/.cache/{source}/` keyed by URL
   hash. Never re-fetch a cached page unless `--refresh`. Runs must be
   resumable after Ctrl-C.
4. **Facts only.** Name, hours, coordinates, address, fee, accessibility,
   phone, official URL. **Never** descriptions, marketing copy, reviews or
   images. Facts are not copyrightable; prose is.
5. Every emitted row carries `_source` (the exact URL) and `_scraped_on`
   (ISO date). Hours go stale and we need to know how old a row is.
6. Hours that don't parse cleanly → `opening_hours: null`,
   `hours_verified: false`, logged with the URL. Never guess, never
   interpolate, never "reasonable default".
7. Tests use saved HTML fixtures. **No network in tests, ever.**
8. Each scraper is a standalone module under `api/scripts/`, importable and
   individually runnable. They share helpers via
   `api/scripts/_scrape_common.py` (fetch+cache, robots check, Hebrew weekday
   parsing, seed-entry emit).

---

## Schema addition, do this first

Add to `Place` in `models.py` and to the TS contract:

```python
season: Literal["year_round", "summer_only"] = "year_round"
```

`summer_only` places must be excluded from itineraries outside April–October.
Add a TODO in `domain/schedule.py`; do not implement the scheduler here.

Reason: water parks and some springs are seasonal. A dataset scraped in August
is confidently wrong in November without this.

---

## Phase 1 — `scrape_parks.py` (highest value, do first)

Source: `parks.org.il` — רשות הטבע והגנים. ~80 national parks and nature
reserves, consistent template, real opening hours. This single source solves
most of the `hours_verified` problem.

1. Fetch the parks/reserves index; extract every site page URL.
2. Per page extract: `name_he` exactly as רט"ג writes it, opening hours per
   weekday, coordinates if present, fee (→ `access`), accessibility if stated.
3. Emit seed entries: `category: "nature"`, `access: "gated"`,
   `hours_verified: true` when hours parsed.
4. Region from coordinates using the existing bbox seam at 31.55. If a page has
   no coordinates, leave `lat`/`lng` null — `fetch_osm.py` will resolve them by
   name.

Hebrew weekday parsing (א׳–ש׳, "פתוח", "סגור", "שעה אחרונה לכניסה") goes in
`_scrape_common.py` — every later scraper needs it.

Expected yield: 60–80 places with verified hours, all three regions.

---

## Phase 2 — `curate_osm_open.py` (free places, no hours needed)

These are `access: "open"` — no gate, no hours, no verification bottleneck.
This is the half of the dataset that makes itineraries feel like real day
trips instead of a museum crawl.

Extend the existing `fetch_osm.py --discover` to also query:

```
natural=spring
natural=water          (lakes/reservoirs, named only)
waterway=waterfall
natural=beach
leisure=nature_reserve (the open ones without a fee tag)
route=hiking           (relations)
tourism=viewpoint
```

Then this script filters `candidates.json` into seed entries:

- accept only elements with `name:he`
- require a significance signal (`wikidata`/`wikipedia` tag, or member of a
  named route) — the low-signal filter already exists, reuse it
- **for `route=hiking`: use the FIRST NODE of the route as the coordinate, not
  the centroid.** A trail is a line; its midpoint is usually a hillside with no
  parking. The trailhead is where a person actually starts.
- emit with `access: "open"`, `opening_hours: null`, `hours_verified: false`
- `category`: spring/water/waterfall/beach → `nature`, viewpoint →
  `viewpoint`, hiking route → `hike`
- `duration_min`: hike 150, others per existing defaults

Write to `seed_names.json`, deduped against existing entries by
`(region, name_he)`.

Expected yield: 40–60 places, zero verification work.

---

## CHECKPOINT — show me before continuing

Run both, then report:

- total places per region
- how many are schedulable (`open`, or `gated` with `hours_verified: true`)
- a sample of 15 random accepted places with name, category, region

Wait for my go-ahead, then continue to Phase 3.

---

## Phase 3 — museums (no scraper)

Write `api/scripts/import_museums.py`. This is an importer, not a scraper —
it reads a file we download, it does not crawl a site.

**Source:** `data.gov.il` dataset 510 — מוזאונים מוכרים. The official list of
museums recognised under חוק המוזאונים, published as government open data. I
will download the CSV/JSON and put it at
`api/data/raw/museums_gov_510.csv`.

The script:

1. Reads that file.
2. Emits seed entries: `category: "museum"`, `access: "gated"`,
   `opening_hours: null`, `hours_verified: false`,
   `_source: "data.gov.il/dataset/510"`.
3. Region from the address/city field, mapped through a small explicit
   city→region dict — **not** guessed from a string. Unmapped cities are
   logged, not assigned.
4. Deduped against existing `seed_names.json` entries by `(region, name_he)`.

Museums land unschedulable by design. Hours come from Phase 4.

Do **not** scrape `museums.gov.il`. The recognised-museums list is a few
hundred entries, most of which are small local collections nobody plans a day
around. Writing a crawler for the tail is not worth it.

---

## Phase 4 — the hand-typed tail (no script, ~1 hour of typing)

Do **not** write scrapers for these. Each lives on its own site; twenty
parsers is a week's work to replace an hour of typing.

This phase is what makes museums and amusement places actually schedulable.

**a. Museum hours (~15 places).** For the museums a person would plan a day
around — Israel Museum, Yad Vashem, Eretz Israel Museum, Tel Aviv Museum of
Art, Bloomfield Science Museum, Madatech, Design Museum Holon, and the
equivalents in each region — open the museum's own site, read the hours, fill
them into the seed entry, set `hours_verified: true`. Everything else from
Phase 3 stays unschedulable and that's fine.

**b. Water parks and luna parks (~15 nationwide).** All get
`season: "summer_only"`. Hours are seasonal and change yearly — set
`hours_verified: true` only for what you personally read today, and set
`_scraped_on` accordingly. `category: "kids"`.

**c. Markets, promenades, city landmarks (~10).** Mostly `access: "open"`.

**d. One `category: "meal"` entry per region.** The planner needs at least one
to satisfy the meal rule in SPEC.md.

All of these go directly into `seed_names.json` with `_source: "manual"` and
`_scraped_on: <today>`.

**Target mix per region** — aim for roughly:
6–10 nature/springs, 3–5 museums, 2–4 viewpoints, 2–3 hikes, 2–3 kids,
1–2 historic, 1 meal. A region of thirty archaeological tels is a failed
dataset even if the count looks good.

---

## Definition of done

- `seed_names.json` has ≥ 90 entries with a realistic category mix per region
  (not 30 archaeological tels).
- `python -m api.scripts.fetch_osm` resolves coordinates for ≥ 70% of them;
  the rest are logged for manual entry.
- Summary prints schedulable count per region.
- `README.md` credits OpenStreetMap (ODbL), רשות הטבע והגנים, and data.gov.il.
- No API keys, `.env`, or `.cache/` committed.

## Out of scope

Scheduler changes, API endpoints, UI, database, `generate_descriptions.py`,
`review_places.py`, and any scraper not listed above.
