# HarbourOS: Norwegian Maritime AIS Port-Call Intelligence

## Project Overview
HarbourOS is a real-time streaming data platform that converts raw AIS
(Automatic Identification System) ship-tracking data from Norway's
BarentsWatch Live API into structured port-call events with a confidence
score. Data flows through a medallion architecture:
- **Bronze**: raw AIS messages (Server-Sent Events from BarentsWatch)
- **Silver**: deduplicated, typed, plausibility-checked messages
- **Gold**: star schema with a port-call fact table and vessel dimensions

Live dashboard: https://harbouros.pages.dev/

## How Claude Should Respond
- Explain things concisely, in plain/layman's terms — no unnecessary jargon
- Propose a specific plan or solution and ask for approval before implementing
- If I push back or give feedback, adjust the plan accordingly rather than re-explaining
- Keep answers focused — don't pad with information I didn't ask for

## Tech Stack
- **Python (uv-managed, 3.12+)** — core language and package management
- **DuckDB / MotherDuck** — embedded analytics database; MotherDuck used for
  the hosted/production database (`HARBOUROS_DB=md:harbouros`)
- **dbt-duckdb** — SQL transformations (staging → marts models)
- **Dagster** — orchestration/dev tooling
- **Pandas** — data manipulation
- **Observable Framework (Node/npm)** — the dashboard app
- **pytest, ruff, black, mypy, pre-commit** — testing and code quality
- **GitHub Actions** — CI (lint/test on PR) + hourly production pipeline cron
- **BarentsWatch Live API** — Norwegian maritime authority AIS data source

## Directory Structure
HarbourOS/
├── src/HarbourOS/ # Core Python package (ingestion, transform, storage, port_calls, state_machine, orchestration)
├── dbt/ # dbt-duckdb project (models/staging, models/marts, seeds, tests)
├── sql/ # Raw SQL for silver-layer inserts
├── scripts/ # Ops/exploration scripts (backfill, migration, data checks)
├── dashboard/ # Observable Framework dashboard app
├── tests/ # pytest suite
└── .github/workflows/ # ci.yml (lint/test) and pipeline.yml (hourly pipeline)

## Running Locally
```bash
uv sync --all-extras
pre-commit install
pytest tests/ -v


## Deployment
Everything deploys automatically from `.github/workflows/pipeline.yml`,
which runs hourly (and can be started by hand from the Actions tab):
1. Ingest live AIS positions → build Silver, states and port calls → `dbt build`
   (all against MotherDuck, `HARBOUROS_DB=md:harbouros`)
2. If all of that succeeds, build the dashboard (`dashboard/`). Its data
   loader `dashboard/src/data/port_calls.csv.py` reads the fresh Gold tables
   from MotherDuck at build time, so no exported CSV is committed.
3. Publish the build to Cloudflare Pages project `harbouros`
   → https://harbouros.pages.dev/

The Cloudflare Pages project is not connected to GitHub, so a `git push` alone
does not update the site; the pipeline run does. A failed run leaves the
previous version live.

Secrets (GitHub repo → Settings → Secrets and variables → Actions):
`MOTHERDUCK_TOKEN`, `BARENTSWATCH_CLIENT_ID`, `BARENTSWATCH_CLIENT_SECRET`,
`CLOUDFLARE_API_TOKEN` (Cloudflare Pages: Edit permission), `CLOUDFLARE_ACCOUNT_ID`.

Manual deploy from a laptop (needs `MOTHERDUCK_TOKEN` in `.env` and
`HARBOUROS_DB=md:harbouros`, plus `npx wrangler login` once):
```bash
cd dashboard && npm run deploy
```


## Making Changes
1. Create a feature branch
2. Make changes locally, run pytest and pre-commit
3. Push and open a PR — CI runs lint/format/typecheck/test automatically
4. I'll review and propose fixes/improvements for your approval
5. Merge to master when ready
