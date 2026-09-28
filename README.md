# HarbourOS: Norwegian Maritime AIS Port-Call Intelligence

HarbourOS turns raw ship positions from Norway's BarentsWatch AIS feed into
port calls: one row per ship stopping once, with the port it stopped at, how
long it stayed, and a confidence score.

**Live dashboard:** https://harbouros.pages.dev/ (updated about every hour)

## How it works

```
BarentsWatch live AIS API
        │  polled every 10 minutes (HarbourOS.collect)
        ▼
Bronze   raw positions, stored as received
        │  SQL: deduplicate, type, plausibility checks
        ▼
Silver   clean positions, plus a Quarantine table with a reason for every rejected row
        │  Python state machine: at sea → approach → berthed/anchored → departed
        ▼
State periods and port calls, each with a confidence score
        │  dbt: match each stop to the nearest UN/LOCODE seaport, run data tests
        ▼
Gold     star schema: fact_port_call, dim_vessel, dim_port, dim_date
        │  Observable Framework data loaders read Gold at build time
        ▼
Dashboard on Cloudflare Pages
```

- **Confidence** reflects whether a ship's own reported status agrees with its
  measured speed. A ship broadcasting "moored" while making 7 knots scores low.
- **Stops more than 10 km from any seaport** (oil rigs, anchorages, fishing
  grounds) are kept but labelled `at_sea` instead of being counted as port calls.
- **The warehouse is MotherDuck** (hosted DuckDB) in production, or a local
  DuckDB file in development.

## Running it

Production runs in GitHub Actions (`.github/workflows/pipeline.yml`). Each run
collects for about an hour, builds every layer, runs the dbt tests, rebuilds
the dashboard, publishes it, and then starts the next run. A failed run opens
a "Pipeline is failing" issue, and the next successful run closes it.

Locally:

```bash
uv sync --all-extras
pre-commit install

# Tests, lint and type checks (the same checks CI runs)
uv run pytest tests/ -q
uv run ruff check src tests
uv run black --check src tests
uv run mypy src/HarbourOS

# Collect for 20 minutes into a local DuckDB file, then build everything.
# Needs BARENTSWATCH_CLIENT_ID and BARENTSWATCH_CLIENT_SECRET in .env.
export HARBOUROS_DB="$PWD/data/ais_bronze.duckdb"
uv run python -m HarbourOS.collect --minutes 20 --every 10
uv run python -m HarbourOS.transform
cd dbt && uv run dbt build && cd ..

# Or run the same chain under Dagster, with its UI
uv run dagster dev -m HarbourOS.orchestration

# Data-quality report on whatever warehouse HARBOUROS_DB points at
uv run python -m HarbourOS.audit
```

`HARBOUROS_DB` chooses the warehouse for both Python and dbt: a file path for
local DuckDB (use an absolute path, since dbt runs from `dbt/`), or
`md:harbouros` for MotherDuck, which also needs `MOTHERDUCK_TOKEN`. When it is
unset, Python uses `data/ais_bronze.duckdb` and dbt uses MotherDuck.

## Tech stack

Python 3.14 (uv), DuckDB and MotherDuck, dbt-duckdb, Dagster (local runs),
Observable Framework, GitHub Actions, Cloudflare Pages, and pytest, ruff,
black and mypy for code quality.

## Repository layout

```
src/HarbourOS/   ingestion, collection, Silver SQL runner, state machine, port calls, audit, Dagster
dbt/             staging and mart models, the UN/LOCODE port seed, data tests
sql/             Silver-layer SQL
dashboard/       Observable Framework site and its data loaders
scripts/         one-off tools (port list fetch, backfill, migration, inspection)
tests/           pytest suite
```
