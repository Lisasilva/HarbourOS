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
Production pipeline runs hourly via GitHub Actions (pipeline.yml) against MotherDuck
Dashboard is live at https://harbouros.pages.dev/
(Note: exact deploy mechanism for the dashboard to this URL isn't fully
captured in the repo yet — worth double-checking/documenting how this
connects to Cloudflare Pages if that's what's serving it)


## Making Changes
1. Create a feature branch
2. Make changes locally, run pytest and pre-commit
3. Push and open a PR — CI runs lint/format/typecheck/test automatically
4. I'll review and propose fixes/improvements for your approval
5. Merge to master when ready
