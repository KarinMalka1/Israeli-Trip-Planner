# Deployment

Backend (`api/`) to Render, frontend (`web/`) to Vercel. Two separate
services, deployed in this order — the backend doesn't need the frontend's
URL to boot, but the frontend needs the backend's URL to work at all, and
the backend needs the frontend's *real* URL for CORS once it exists.

1. Deploy the backend to Render first (its URL doesn't depend on anything).
2. Deploy the frontend to Vercel, pointed at that Render URL.
3. Go back to Render and set `ALLOWED_ORIGINS` to the real Vercel URL,
   which triggers a redeploy.

Nothing here deploys anything by itself — every step below is done by hand
in each dashboard.

## 1. Backend → Render

This repo has a `render.yaml` at the root (a Render "Blueprint"), so the
easiest path is:

1. Render dashboard → **New** → **Blueprint** → connect this repo.
2. Render reads `render.yaml` and proposes one web service
   (`israeli-trip-planner-api`) with the build/start commands already
   filled in. Confirm and deploy.
3. Once it's live, copy the service's URL (`https://israeli-trip-planner-api.onrender.com`
   or whatever Render assigned) — the frontend needs it in step 2.

If you'd rather create the service by hand instead of using the Blueprint
(same result, more visible):

- **Root directory**: repo root (leave blank) — not `api/`. `api/main.py`
  imports as `from api.domain import schedule` etc., which only resolves
  when the process starts from the directory that *contains* the `api/`
  package, not from inside it.
- **Build command**: `pip install -r api/requirements.txt`
- **Start command**: `uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}`
  — Render always sets `$PORT`; the `:-8000` half is what makes this same
  command still bind to 8000 if it's ever run somewhere that doesn't set it.
- **Environment variables**: see the table below.

`api/requirements.txt` only lists what the running API actually imports
(`fastapi`, `uvicorn`, `pydantic`) plus test deps (`pytest`, `httpx`) —
`requests` and `pyproj` are there too but are scraper-only
(`api/scripts/*.py`, never imported by `main.py`); Render installing them
is harmless, just not required for the API to boot.

Place data (`api/data/places/*.json`) and the precomputed distance matrix
(`api/data/distance_matrix.json`) are committed to the repo, not generated
at build time, so no extra build step is needed to seed them.

## 2. Frontend → Vercel

1. Vercel dashboard → **Add New** → **Project** → import this repo.
2. **Root Directory**: set it to `web` (Vercel's project settings, not a
   config file — see "why no vercel.json" below).
3. Framework preset: Vercel auto-detects Vite once Root Directory is set
   to `web`. Build command `npm run build`, output directory `dist` —
   leave both on the auto-detected defaults.
4. **Environment variable**: `VITE_API_BASE_URL` = the Render URL from
   step 1 (e.g. `https://israeli-trip-planner-api.onrender.com`, no
   trailing slash — `client.ts` appends paths starting with `/`).
5. Deploy. Copy the resulting Vercel URL (e.g.
   `https://israeli-trip-planner.vercel.app`) for step 3.

### Why no `vercel.json`

The app is a single-route SPA with no client-side router (no react-router
or similar in `package.json`) — there is no second route that needs a
rewrite-to-`index.html` fallback, which is the usual reason a Vite/React
project needs one. Vercel's zero-config Vite detection handles the build
and output directory on its own once Root Directory is set. If routing is
added later, that's when a `vercel.json` with a catch-all rewrite earns
its place — adding one now would be config with nothing to do.

## 3. Close the loop: tell Render about the real Vercel URL

`ALLOWED_ORIGINS` on Render still says `http://localhost:5173` (its
default) until you set it — the API works, but the deployed frontend's
requests will fail CORS until this step:

1. Render dashboard → the API service → **Environment**.
2. Set `ALLOWED_ORIGINS` to the Vercel URL from step 2 (e.g.
   `https://israeli-trip-planner.vercel.app`). Comma-separate if you also
   want to keep local dev working against the deployed API, or if Vercel
   gives you both a production domain and a preview-deployment domain you
   want to allow:
   `https://israeli-trip-planner.vercel.app,http://localhost:5173`
3. Saving triggers a redeploy automatically.

## Environment variables — what goes where

| Variable | Set on | Local dev default | Production value |
|---|---|---|---|
| `ALLOWED_ORIGINS` | Render (backend) | `http://localhost:5173` (main.py's own default — no `.env` needed locally) | the Vercel URL from step 2, comma-separated if more than one |
| `VITE_API_BASE_URL` | Vercel (frontend) | `http://localhost:8000` (client.ts's own default) | the Render URL from step 1 |
| `PORT` | Render sets this itself | n/a | n/a — don't set it by hand |
| `PYTHON_VERSION` | Render (backend) | n/a | `3.14.2` in `render.yaml`, matching local dev — lower it if Render's available versions don't include 3.14 yet |

Neither default needs a local `.env` file to work — they're only there
for when you deploy. `web/.env.local` (gitignored, via `web/.gitignore`'s
`*.local` rule) is where a personal override goes if you ever want one
locally, same as the existing one pointing at a LAN IP for phone testing.

## Verifying it worked

```bash
# Backend is up and has real data (not an empty seed/matrix):
curl https://<your-render-url>/api/health
# {"status":"ok","places":<a real count>,"matrix_loaded":true}

# CORS actually allows the deployed frontend's origin:
curl -i -X OPTIONS https://<your-render-url>/api/places \
  -H "Origin: https://<your-vercel-url>" \
  -H "Access-Control-Request-Method: GET" \
  | grep -i access-control-allow-origin
# should echo back https://<your-vercel-url>
```

Then open the Vercel URL itself and confirm a day plan actually loads —
that exercises the full path (frontend → `VITE_API_BASE_URL` → Render →
CORS allow → response), not just each half in isolation.
