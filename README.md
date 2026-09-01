# Israeli Day Trip Planner

A day-trip itinerary planner for Israel: `api/` is a FastAPI backend, `web/` is a React + Vite frontend.

## Data sources

Place data is seeded from [OpenStreetMap](https://www.openstreetmap.org/copyright) (© OpenStreetMap contributors, [ODbL](https://opendatacommons.org/licenses/odbl/)), רשות הטבע והגנים ([parks.org.il](https://www.parks.org.il/)), and [data.gov.il](https://data.gov.il/).

Place photos (up to 3 per place, `Place.images`) are sourced from [Wikimedia Commons](https://commons.wikimedia.org/) only, downloaded into `web/public/images/` and committed — never hotlinked, never fetched at request time. Most were pulled in by `api/scripts/fetch_commons_images.py`, an offline batch tool (`--dry-run` to preview, otherwise run and review the printed match report before committing — a wrong photo with a correct credit is still wrong); the rest were picked by hand the same way for the earliest pilot place. Either way, only CC0, CC BY, CC BY-SA or public-domain images are accepted, and only when Commons' own extmetadata supplies both an artist and a license — every image carries its photographer/uploader, license and a link back to its Commons file page (`PlaceImage.credit` / `source_url`), and the schema refuses to construct an image missing either. Expect a low hit rate: national parks and heritage sites tend to have Commons coverage, restaurants and small reserves usually don't — the empty-state fallback panel is the common case, not an edge case.

## Data layout

Places live in `api/data/places/`, one `*.json` file per region (`north.json`, `central.json`, `south.json`) rather than one shared file — see SPEC.md section 12. `PlaceRepository.load()` reads every file in that directory and fails loudly at startup on a misfiled or duplicated entry, so edit only the file for the region you're touching.
