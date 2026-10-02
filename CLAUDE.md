# HarbourOS: Norwegian Maritime AIS Port-Call Intelligence

## Project Overview
HarbourOS is a micro-batch data pipeline that converts raw AIS
(Automatic Identification System) ship-tracking data from Norway's
BarentsWatch Live API into structured port-call events with a confidence
score. It polls the API's REST endpoint every 10 minutes (not a stream) and
publishes about 4 times a day. Data flows through a medallion architecture:
- **Bronze**: raw AIS positions, as received from BarentsWatch
- **Silver**: deduplicated, typed, plausibility-checked messages
- **Gold**: star schema with a port-call fact table, a vessel-track fact
  table and vessel, port and date dimensions

The README is the short overview; `docs/engineering-diary.md` is the full
history, design decisions and interview notes. Keep both in step with the code.

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
- **GitHub Actions** — CI (lint/test on PR) + the production pipeline (about 4 runs a day)
- **BarentsWatch Live API** — Norwegian maritime authority AIS data source

## Directory Structure
HarbourOS/
├── src/HarbourOS/ # Core Python package (ingestion, transform, storage, port_calls, state_machine, orchestration)
├── dbt/ # dbt-duckdb project (models/staging, models/marts, seeds, tests)
├── sql/ # Raw SQL for silver-layer inserts
├── scripts/ # Ops/exploration scripts (backfill, migration, data checks)
├── dashboard/ # Observable Framework dashboard app
├── tests/ # pytest suite
├── docs/ # engineering diary (full project history and design notes)
└── .github/workflows/ # ci.yml, pipeline.yml (production), preview.yml, audit.yml

## Running Locally
```bash
uv sync
pre-commit install
pytest tests/ -v
```

## Deployment
Everything deploys automatically from `.github/workflows/pipeline.yml`
(and it can be started by hand from the Actions tab). Each run:
1. Collects live AIS positions every 10 minutes for 330 minutes
   (`HarbourOS.collect`), uploads them to Bronze in one go, then builds Silver,
   states and port calls and runs `dbt build` (all against MotherDuck,
   `HARBOUROS_DB=md:harbouros`), then checks 100 random port calls from the run
   against OpenStreetMap's quays (`HarbourOS.reliability`; its tile on the site is hidden until the
   improved check is measured; it never blocks the deploy). Each run on `master` starts the next one when
   it ends (a 6-hourly schedule only restarts the chain if it breaks, e.g.
   after a run is cancelled by hand), so collection is continuous and the site
   updates about 4 times a day. Hourly runs (50 minutes of collection) were
   tried on 2026-09-28/29 and dropped: they grew MotherDuck storage to 2.5 GB
   in a day and a half, mostly "failsafe" copies of rewritten tables, which
   count toward the 10 GB allowance. For a quick manual
   test, set "collect_minutes" to something small.
2. If all of that succeeds, build the dashboard (`dashboard/`). Its data
   loader `dashboard/src/data/port_calls.csv.py` reads the fresh Gold tables
   from MotherDuck at build time, so no exported CSV is committed.
3. Publish the build to Cloudflare Pages project `harbouros`
   → https://harbouros.pages.dev/
4. On `master` only: if any step failed, open (or comment on) a GitHub issue
   titled "Pipeline is failing", assigned to Maria, so GitHub emails her. The
   next successful run closes it.

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

## Working Agreements
- Maria merges every PR herself. Claude opens PRs and never merges them.
- Keep the `Co-Authored-By: Claude ...` trailer on commits (her decision).
- Never change anything that costs money or needs a credit card. Cloudflare,
  MotherDuck and the GitHub secrets are hers: tell her what to change, and
  don't change them yourself.
- To test pipeline changes safely, run the Pipeline workflow by hand on the PR
  branch (Actions → Pipeline → Run workflow → pick the branch). A branch other
  than `master` publishes only to a Cloudflare *preview* address, so the live
  site stays untouched.
- To test dashboard changes, push to a branch named `dashboard-*`. The
  "Dashboard preview" workflow reads master's Gold tables (it rebuilds
  `dim_vessel` and `fct_vessel_track` only if the branch changes `dbt/`),
  builds the site and publishes it to `https://<branch>.harbouros.pages.dev`,
  without collecting data or queueing behind the pipeline runs. It also pushes
  screenshots and a page-error report to the `preview-screenshots` branch
  (folder per branch), which is how Claude checks a preview it can't open.

## Known Facts and Gotchas
- **Production is `pipeline.yml`, not Dagster.** `src/HarbourOS/orchestration.py`
  is the same chain for local runs (`uv run dagster dev -m HarbourOS.orchestration`)
  and production doesn't run it. It keeps every ship (the old 100-ship cap was
  removed on 2026-09-28).
- **GitHub cron is unreliable.** Hourly top-of-the-hour runs actually started
  every 3–6 hours, and on 2026-09-28 an hourly :17 trigger skipped almost two
  hours. That is why collection happens *inside* a run (a snapshot every 10
  minutes) and each run starts the next one itself: the state machine only
  joins sightings at most 30 minutes apart (`MAX_GAP`). A run started by
  `GITHUB_TOKEN` via `workflow_dispatch` is one of the few events GitHub lets
  that token trigger.
- **Stay inside MotherDuck's free plan (10 compute hours a month).** Snapshots
  are uploaded once per run, and state periods are rebuilt only from each
  ship's latest believable period (see `run_state_periods_transform`), so the
  work per run doesn't grow with history. Port calls are still rebuilt from
  every period of each changed ship. Watch that if usage climbs.
- **`fact_port_call` is incremental.** Each run re-matches only ships with new
  data. After changing the port list (`dbt/seeds`, from `scripts/fetch_ports.py`)
  or the matching rules, start the Pipeline by hand on `master` with
  "full_refresh" ticked, so older port calls are matched again. After changing
  the rules in `port_calls.py`, raise its `RULES_VERSION` instead: the next run
  then re-derives every ship's port calls and rebuilds the fact table by itself.
- **Root `.gitignore` ignores every `data/` folder.** The dashboard's loaders
  in `dashboard/src/data/*.py` are re-included explicitly, and CSVs there stay
  ignored.
- **Cloudflare Pages project `harbouros`** is direct-upload (not Git-connected),
  with production branch `master`. The free plan's 500 builds/month limit covers
  Git builds. Direct uploads made with wrangler reportedly don't count (per
  Cloudflare community answers, not official docs).
- **The dashboard build downloads its libraries from npm.** Run #106
  (2026-10-01) failed when npm didn't answer, so the workflows keep those
  libraries in a GitHub cache (`dashboard/src/.observablehq/cache/_npm`) and
  retry the build up to three times.
- **Port calls need an official berth.** `dbt/seeds/kystverket_locations.csv`
  is Kystverket's location register (quays, harbours, anchorages), from
  `scripts/fetch_kystverket_locations.py`. After refreshing it, start the
  Pipeline by hand with "full_refresh" ticked. Being near a listed berth must
  never count as confirmation in the reliability check (it would be marking
  its own homework): the check uses OpenStreetMap and the ships' official
  voyage reports from Kystdatahuset, and reads the register only to place the
  reported names (anonymous access covers ships of 45 m and longer).
- The dashboard builds on Node 24. The workflow actions are on Node-24 majors
  (checkout, setup-python, setup-uv and setup-node @v7, wrangler-action @v4).
- Python 3.14 is used in CI. dagster-dbt is avoided because it pins an older
  dbt that breaks on 3.14 (see the orchestration.py docstring).

## Roadmap (agreed with Maria, 2026-09-26)
Done:
- Automatic dashboard deploy (PR #1), Node 24 actions (PR #2).
- 1. Pipeline runs on time: 10-minute collection (PR #4), each run starting the
  next (PR #11).
- 2. Data-quality audit (PR #5), its fixes (PRs #6-#8), and a re-run on
  2026-09-28.
- 3. "Data last updated" on the dashboard (PR #12) and a failure alert issue
  (PR #11).
- 4. Dagster's 100-ship cap removed; GitHub Actions is production, Dagster is
  for local runs.
- 5. README rewritten (PR #13), then again with the engineering diary.
- 6. Dashboard redesign: live map, routes, replay, search (PRs #14, #16-#19),
  and the completeness fix (PR #15).
- 7. Automatic reliability check against OpenStreetMap, shown on the site
  (PR #25), and fish-farm stops labelled instead of counted as port calls
  (PR #26).

Next:
- AI features, designed but not built: collect the AIS destination field,
  resolve destinations to UN/LOCODE, then a slim unusual-behaviour stage;
  ETA prediction later (see the diary's AI roadmap).
- Quays missing from UN/LOCODE (~1,600 stops 10-25 km from a listed port in
  the 2026-09-28 audit).
- Re-run the data audit.
