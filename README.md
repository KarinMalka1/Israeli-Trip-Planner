# Israeli Day Trip Planner

A day-trip itinerary planner for Israel: `api/` is a FastAPI backend, `web/` is a React + Vite frontend.

## Data sources

Place data is seeded from [OpenStreetMap](https://www.openstreetmap.org/copyright) (© OpenStreetMap contributors, [ODbL](https://opendatacommons.org/licenses/odbl/)), רשות הטבע והגנים ([parks.org.il](https://www.parks.org.il/)), and [data.gov.il](https://data.gov.il/).

Place photos (up to 3 per place, `Place.images`) are sourced from [Wikimedia Commons](https://commons.wikimedia.org/) only, downloaded by hand into `web/public/images/` and committed — never hotlinked, never fetched at request time. Most Commons files are CC BY or CC BY-SA, which legally require attribution: every image carries its photographer/uploader, license and a link back to its Commons file page (`PlaceImage.credit` / `source_url`), and the schema refuses to construct an image missing either.

## Data layout

Places live in `api/data/places/`, one `*.json` file per region (`north.json`, `central.json`, `south.json`) rather than one shared file — see SPEC.md section 12. `PlaceRepository.load()` reads every file in that directory and fails loudly at startup on a misfiled or duplicated entry, so edit only the file for the region you're touching.
