# HarbourOS dashboard

The public site at https://harbouros.pages.dev/, built with
[Observable Framework](https://observablehq.com/framework/). It is one page
(`src/index.md`): a live map of ships on the Norwegian coast, each ship's route
over the last 3 days, a 24-hour replay, port and ship search, and charts of the
week's port calls.

## Where the data comes from

Framework runs the Python data loaders in `src/data/` at build time, through
`uv run python` (see `interpreters` in `observablehq.config.js`), so they can
import `HarbourOS` and read the warehouse named by `HARBOUROS_DB`:

| Loader | Serves | Reads |
|---|---|---|
| `port_calls.csv.py` | every port call, with ship and port names and an `in_port_now` flag | `fact_port_call`, `dim_vessel`, `dim_port` |
| `ships.csv.py` | each ship's latest position and last port | `fct_vessel_track`, `fact_port_call` |
| `tracks.parquet.py` | the last 3 days of routes, thinned to points where a ship moved | `fct_vessel_track` |
| `freshness.json.py` | when the newest AIS reading was, and when the build ran | `ais_messages_silver` |

The built site is static files only; no database is reachable from it.

## Commands

Run from this folder, with `HARBOUROS_DB` (and `MOTHERDUCK_TOKEN` for
MotherDuck) set in the repo's `.env` or the environment:

| Command | What it does |
|---|---|
| `npm ci` | install dependencies |
| `npm run dev` | local preview at http://localhost:3000 |
| `npm run build` | build the static site into `dist/` |
| `npm run deploy` | build and upload to Cloudflare Pages (needs `npx wrangler login`) |
| `npm run clean` | clear the data-loader cache |

In production the Pipeline workflow builds and deploys the site after each
successful run. Pushing a branch named `dashboard-*` publishes a preview to
`https://<branch>.harbouros.pages.dev` (see `.github/workflows/preview.yml`),
and `scripts/screenshots.mjs` takes screenshots of it.
