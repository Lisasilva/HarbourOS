# HarbourOS: port calls from Norwegian AIS data

HarbourOS turns the raw positions that ships broadcast along the Norwegian coast
(AIS, via the BarentsWatch API) into **port calls**: one row per ship stopping
once, with the port it stopped at, how long it stayed, whether we saw it arrive
and leave, and a **confidence score**. A live map dashboard is rebuilt from the
results about four times a day.

**Live dashboard:** https://harbouros.pages.dev/

## Why it exists

AIS tells you where a ship is, not what it is doing. "Did this ship call at
Bergen, and for how long?" is not in the feed. Answering it means cleaning a
noisy stream, stitching thousands of separate position reports into visits, and
matching each stop to a real port.

The feed is also partly typed in by hand. A ship's navigational status
("moored", "under way") is set by the crew and is often wrong: one ship in the
data reported "moored" while averaging 7 knots for 11 hours. HarbourOS
therefore decides whether a ship is stopped from its GPS speed, and uses the
crew's status only to raise or lower a confidence score.

This is a portfolio data-engineering project by Maria. It is built to run
entirely on free tiers (GitHub Actions, MotherDuck, Cloudflare Pages).

## Architecture

```
BarentsWatch live AIS API (OAuth2, REST)
        │  polled every 10 minutes for ~5.5 hours per run (HarbourOS.collect)
        ▼
Bronze   ais_messages_bronze: every position as received (one upload per run)
        │  SQL, incremental: deduplicate, validate, rename
        ▼
Silver   ais_messages_silver + ais_messages_quarantine (every rejected row, with a reason)
        │  Python state machine, per ship, incremental
        ▼
         ship_state_periods (at_sea / approach / berthed / anchored / departed)
         port_call_events   (one row per visit, confidence, completeness)
        │  dbt: match each stop to the nearest UN/LOCODE seaport, run data tests
        ▼
Gold     fact_port_call, fct_vessel_track, dim_vessel, dim_port, dim_date
        │  Observable Framework data loaders query Gold at build time
        ▼
Static dashboard on Cloudflare Pages (map, routes, 24-hour replay, charts)
```

- **Why Python for the middle step:** turning a ship's readings into stops is
  sequential logic (debouncing, gaps, "approach" vs "departed"), which is hard
  to express in SQL. Everything that is a join or an aggregate stays in SQL.
- **Confidence:** each reading scores 1.0 when speed and the crew's status
  agree, 0.5 when there is no usable status, and 0.3 when they contradict.
  A visit's score is the reading-weighted average.
- **Honest labels:** stops more than 10 km from any listed seaport (oil rigs,
  offshore anchorages, fishing grounds) are kept as `at_sea`, not counted as
  port calls. Visits whose arrival or departure was not seen are labelled so.
- **Incremental everywhere:** each layer only processes new data, so the work
  per run stays flat as history grows.

More detail is in [`docs/engineering-diary.md`](docs/engineering-diary.md).

## Tech stack

| Area | Tools |
|---|---|
| Language and packaging | Python 3.14 (3.12+ supported), uv |
| Warehouse | DuckDB locally, MotherDuck (hosted DuckDB) in production |
| Transformations | SQL files for Silver, Python for the state machine, dbt-duckdb for Gold |
| Orchestration | GitHub Actions (production); Dagster for local runs |
| Dashboard | Observable Framework, Observable Plot, MapLibre GL 5, OpenFreeMap tiles |
| Hosting | Cloudflare Pages (direct upload with wrangler) |
| Quality | pytest (64 tests), dbt data tests, ruff, black, mypy, pre-commit |

## Running it locally

Prerequisites: Python 3.12+, [uv](https://docs.astral.sh/uv/), and Node 24 for
the dashboard. Live data needs a free BarentsWatch API client
([barentswatch.no](https://www.barentswatch.no/)).

```bash
uv sync
pre-commit install          # optional

# The same checks CI runs
uv run pytest tests/ -q
uv run ruff check src tests
uv run black --check src tests
uv run mypy src/HarbourOS
```

To reproduce the pipeline against a local DuckDB file (run from the repo root,
because the Silver SQL is read from `sql/`):

```bash
cp .env.example .env        # then fill in the BarentsWatch credentials
mkdir -p data
export HARBOUROS_DB="$PWD/data/ais_bronze.duckdb"   # absolute: dbt runs from dbt/

uv run python -m HarbourOS.collect --minutes 30 --every 10   # Bronze
uv run python -m HarbourOS.transform                         # Silver, states, port calls
(cd dbt && uv run dbt build)                                 # Gold + data tests
uv run python -m HarbourOS.audit                             # optional data-quality report

cd dashboard && npm ci && npm run dev                        # http://localhost:3000
```

A visit needs at least three readings joined by gaps of 30 minutes or less, so
collect for 30 minutes or more to see port calls. The same chain also runs
under Dagster with its UI: `uv run dagster dev -m HarbourOS.orchestration`.

## Configuration

| Variable | Needed for | Notes |
|---|---|---|
| `HARBOUROS_DB` | everything | A DuckDB file path, or `md:harbouros` for MotherDuck. If unset, Python uses `data/ais_bronze.duckdb` and dbt uses `md:harbouros`. |
| `BARENTSWATCH_CLIENT_ID`, `BARENTSWATCH_CLIENT_SECRET` | collecting data | OAuth2 client credentials, scope `ais` |
| `MOTHERDUCK_TOKEN` | MotherDuck only | In GitHub Actions it is set as lowercase `motherduck_token` |
| `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` | deploying | GitHub Actions secrets only |

Locally these go in `.env` (see `.env.example`); in production they are GitHub
Actions secrets.

## How the deployed app works

Production is `.github/workflows/pipeline.yml`. Each run:

1. collects a snapshot of every ship every 10 minutes for 330 minutes and
   uploads them to Bronze in one statement;
2. builds Silver, state periods and port calls, then `dbt build` (models and
   tests) on MotherDuck;
3. builds the dashboard (its Python data loaders query Gold) and publishes it
   to Cloudflare Pages, only if every earlier step passed;
4. opens or updates a "Pipeline is failing" GitHub issue on failure, and closes
   it on the next success;
5. starts the next run itself, so collection is continuous. A 6-hourly cron
   only restarts the chain if it breaks.

The site is static: visitors download pre-built CSV, JSON and Parquet files,
and no database is exposed. A failed run leaves the previous version live.
Other workflows: `ci.yml` (lint, types, tests on every PR), `preview.yml`
(builds `dashboard-*` branches to a preview address without collecting), and
`audit.yml` (a manual, read-only data-quality report).

## Repository layout

```
src/HarbourOS/   collect and ingestion, storage (warehouse I/O), transform (Silver runner and
                 incremental rebuilds), state_machine, port_calls, audit, orchestration (Dagster)
sql/             Silver accept/reject rules
dbt/             staging and mart models, UN/LOCODE port seed, data tests
dashboard/       Observable Framework site and its Python data loaders
scripts/         one-off tools (port list fetch, historic backfill, migration, inspection)
tests/           pytest suite
docs/            engineering diary
.github/         CI, pipeline, dashboard preview and audit workflows
```
