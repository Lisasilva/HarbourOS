# HarbourOS: Project Documentation and Engineering Diary

*Maria's personal reference. Written 2026-09-30, from the code on `master`
(commit `8a07b1e`), the git history, the pipeline logs and the project notes.*

The README is the short public overview. This file is everything else: why the
project looks the way it does, how every part works, what went wrong, and how
to talk about it.

**How to read the facts in here.** Numbers come with a date and a source.
Where something is not recorded anywhere (in the repo, the logs or the project
notes), it says **Not recorded** instead of guessing. Where a statement is a
reasoned guess, it says *(inferred)*.

---

## Contents

1. [Project origin and planning](#1-project-origin-and-planning)
2. [Complete architecture](#2-complete-architecture)
3. [Data engineering implementation](#3-data-engineering-implementation)
4. [Database and storage](#4-database-and-storage)
5. [Data engineering concepts, where they live](#5-data-engineering-concepts-where-they-live)
6. [Performance optimisation](#6-performance-optimisation)
7. [Problems faced during development](#7-problems-faced-during-development)
8. [Current project status](#8-current-project-status)
9. [AI integration roadmap](#9-ai-integration-roadmap)
10. [Interview preparation](#10-interview-preparation)
11. [LinkedIn content ideas](#11-linkedin-content-ideas)
12. [Appendix: timeline, numbers and file index](#12-appendix)

---

## 1. Project origin and planning

### Why this idea

**Not recorded:** the original reason for choosing AIS data and port calls is
not written down in the repo or in this project's chats (the first planning
conversations happened in chats outside this project). Worth adding a
paragraph here in your own words, because "why did you pick this?" is the
first interview question.

What the history does show about the intent:

- The project was framed from the first commit as *"Norwegian maritime AIS
  port-call intelligence"* (`pyproject.toml`), with a **confidence score per
  port call** as the headline output.
- It was planned as a day-by-day build ("Day 2: Bronze", "Day 3: Silver" ...
  "Day 8: dashboard" in the commit messages), following a medallion
  architecture from the start.
- A hard constraint appears early and is repeated throughout: **free, open
  source and self-hostable**. The Day 8 commit rejects a tool because it
  "conflicts with the free, open-source, self-hosted constraint", and later
  work kept everything inside free tiers (GitHub Actions, MotherDuck free
  plan, Cloudflare Pages).
- Chatbot-style features ("ask your data") were **rejected deliberately**
  early on, in favour of AI that solves a real data problem (your own summary
  in the "Diagnose pipeline failure" thread, 2026-09-29).

### The original problem statement

Reconstructed from the code and commit messages:

> AIS tells you where every ship is every few seconds, but not what it is
> doing. Turn that raw stream into **port calls** (which ship stopped where,
> arriving when, leaving when), and say **how much each one can be trusted**,
> because part of the feed is typed in by crews and is often wrong.

The key insight, written in `src/HarbourOS/state_machine.py`: the ship's
*navigational status* is typed by the crew and is "often stale or wrong"
(observed: a ship reporting "moored" while averaging 7 knots for 11 hours),
while *speed over ground* is measured by GPS. So the design is **speed-first**,
and the crew's status is only used as corroborating evidence for the
confidence score.

### Initial goals

From the Day 1–8 commits:

1. Ingest live AIS from BarentsWatch into a raw Bronze layer.
2. Clean it in a Silver layer where no row silently disappears (every rejected
   row goes to Quarantine with a reason).
3. Derive ship states and port calls with a confidence score.
4. Model a Gold star schema in dbt with a UN/LOCODE port dimension.
5. Orchestrate the chain (Dagster).
6. Publish an interactive dashboard.

### Design decisions, alternatives rejected, and why

| Decision | Alternative(s) rejected | Why (source) |
|---|---|---|
| Speed decides the state; crew status only adjusts confidence | Trust `navigational_status` | Status is crew-typed and often wrong (Day 4 commit, `state_machine.py` docstring) |
| State machine in **Python** | Pure SQL | It is sequential logic (debounce, gaps, naming "approach" vs "departed") that SQL can't express cleanly. Anything that is a join or aggregate stays in SQL (`dbt/models/sources.yml`, Day 6 commit) |
| Stop **position** recovered in SQL by joining back to Silver | Carry lat/lon through the state machine | "Anything derivable by joining belongs in the warehouse" (Day 6 commit, `stg_port_calls.sql`) |
| Port match with **Haversine in plain SQL** | DuckDB's spatial extension | One formula, no runtime dependency, and exact enough for port positions that are themselves only accurate to ~2 km (`fact_port_call.sql`) |
| **UN/LOCODE** as the port list, committed as a dbt seed | Not recorded | It is the UN's official code list; LOCODE is a real business key, so no surrogate key is needed (`dim_port.sql`, `scripts/fetch_ports.py`) |
| Silver rules in **one SQL file** producing a `rejection_reason` | Separate accept and reject queries | Silver and Quarantine can't drift apart when one CASE decides both (`sql/silver_stage_new_batch.sql`) |
| `missing_*` vs `invalid_*` rejection reasons | One generic "bad row" reason | A field the source never sent points at the feed; a field sent wrong points at the ship's equipment (commit b1d5680) |
| **Observable Framework** for the dashboard | **Evidence** | Evidence 0.9 removed static export, so publishing needed their hosted platform. Framework outputs plain HTML/CSS/JS that any static host can serve (Day 8 commit) |
| **Cloudflare Pages** | GitHub Pages | Per your notes, partly so a future API key could live in a Cloudflare Pages Function rather than in the page's JavaScript. That function was never needed and does not exist |
| dbt as a **subprocess** inside Dagster | `dagster-dbt` integration | `dagster-dbt` pins dbt-core below 1.12, which pulls a `mashumaro` version that crashes on Python 3.14 (`orchestration.py` docstring) |
| **MotherDuck** for production | Keep a local DuckDB file | The pipeline had to run in GitHub Actions, which has no persistent disk. MotherDuck is the same engine, so the switch is a connection string (`storage.py`). The deeper reasoning at the time is **not recorded** |
| **GitHub Actions** as the production scheduler | Dagster in production | Free, already used for CI, no server to host. Dagster stays for local runs with its UI |
| **Micro-batch polling** of the REST endpoint | A true streaming consumer | **Not recorded** why streaming wasn't used. *(Inferred:* a stream needs an always-on process, which the free-tier setup doesn't have; the 10-minute poll is enough for stops that last tens of minutes to days.) |
| Dashboard reads a **static snapshot** built at deploy time | A live API querying the warehouse | No database is exposed to the internet, and hosting is free (dashboard "How this was built" note) |

### Why these tools

- **Python + uv:** the language for the ingestion and state logic; uv for fast,
  locked, reproducible installs (`uv.lock`).
- **DuckDB / MotherDuck:** an analytical (columnar) database that runs as a
  library, so tests use a throwaway file and production uses the hosted
  version with the same SQL.
- **dbt-duckdb:** versioned, tested SQL models with incremental materialisation
  and built-in tests (`unique`, `not_null`, `relationships`, `accepted_values`).
- **Dagster:** asset-based orchestration with lineage and a UI, used for local
  runs.
- **Observable Framework + Plot + MapLibre:** a static site generator with data
  loaders that run at build time; MapLibre for the interactive vector map;
  OpenFreeMap for free tiles without an API key.
- **GitHub Actions:** CI, the production pipeline, previews and the audit.
- **pytest, ruff, black, mypy, pre-commit:** code quality, run the same way on
  a laptop and in CI.

### How the architecture evolved

| Phase | Dates | What changed |
|---|---|---|
| 1. Local medallion build | 2026-08-29 → 09-10 | Scaffold, Bronze (100 ships per call), Silver with Quarantine, one-off 24-hour historic backfill (~28k rows), state machine, port calls, dbt Gold, Dagster, first dashboard deployed by hand |
| 2. Hardening | 09-10 → 09-14 | Stopped dropping Bronze on each ingest; batched inserts; incremental Silver; per-ship rebuilds; incremental `fact_port_call`; removed the 100-ship cap |
| 3. Cloud | 09-16 → 09-22 | Warehouse moved to MotherDuck; pipeline in GitHub Actions; network round trips cut from ~17,000 to ~40; "hourly" schedule |
| 4. Continuous and automated | 09-26 → 09-28 | Automatic dashboard deploy (PR #1); 10-minute collection inside long runs (PR #4); data audit (PR #5) and its fixes (PRs #6–#8); auto full refresh on schema change (PR #9); self-chaining runs and failure alerts (PR #11); "last updated" (PR #12) |
| 5. Map dashboard | 09-28 → 09-30 | `ship_category` and `fct_vessel_track`; live map, routes, replay, search (PRs #14, #16–#19); completeness fix and `RULES_VERSION` (PR #15); back to ~4 runs a day for storage (PR #18) |

---

## 2. Complete architecture

### End to end

```
                    ┌──────────────────────────────────────────────────────────┐
                    │ GitHub Actions: pipeline.yml (one run ≈ 5.5 h)           │
                    │                                                          │
 BarentsWatch  ───► │ 1 collect.py   poll every 10 min, drop repeats,          │
 OAuth2 + REST      │                upload all polls once (Parquet → INSERT)  │
                    │ 2 transform.py Silver SQL → state machine → port calls   │
                    │ 3 dbt build    staging views → Gold tables → 32 tests    │
                    │ 4 npm build    data loaders query Gold → static site     │
                    │ 5 wrangler     upload to Cloudflare Pages                │
                    │ 6 gh           failure issue open/close; start next run  │
                    └───────────┬───────────────────────────────┬──────────────┘
                                │ reads/writes                  │ uploads
                                ▼                               ▼
                     MotherDuck (hosted DuckDB)       Cloudflare Pages (static)
                     Bronze / Silver / Python-owned        harbouros.pages.dev
                     derived tables / dbt Gold             ▲
                                                           │ browser downloads
                                                           │ CSV, JSON, Parquet
                                                         visitor
```

### Components and how they talk to each other

| Component | Role | Talks to | How |
|---|---|---|---|
| `ingestion.py` | OAuth2 token + `GET /v1/latest/combined` | BarentsWatch | HTTPS, `requests`, 60 s timeout |
| `collect.py` | Polls every 10 min, removes repeats, uploads once | `ingestion.py`, `storage.py` | In-process calls |
| `storage.py` | Every warehouse write; `connect()` picks local file or MotherDuck from `HARBOUROS_DB` | DuckDB / MotherDuck | DuckDB Python client |
| `sql/*.sql` | Silver accept/reject rules | Warehouse | Run by `transform.py` |
| `state_machine.py` | Readings → state periods (pure functions) | nothing (pure) | Called by `transform.py` |
| `port_calls.py` | State periods → visits (pure functions) | nothing (pure) | Called by `transform.py` |
| `transform.py` | Runs Silver, then per-ship incremental rebuilds | `storage.py`, the two pure modules | In-process |
| `dbt/` | Staging views and Gold tables, data tests | Warehouse | `dbt build` (dbt-duckdb) |
| `dashboard/src/data/*.py` | Build-time data loaders | Warehouse | `uv run python` from Observable Framework |
| `dashboard/src/index.md` | The page: map, panels, charts | Files produced by the loaders | Browser `fetch` of static files |
| `pipeline.yml` | Production orchestration | All of the above, GitHub API, Cloudflare | Steps in one job |
| `orchestration.py` | Same chain as Dagster assets, for local runs | Same modules | Dagster |
| `audit.py` | Read-only data-quality report | Warehouse | 14 SELECT checks → Markdown |

The warehouse is the only thing shared between steps. Python owns Bronze,
Silver, Quarantine, `ship_state_periods`, `port_call_events`,
`derived_progress` and `derived_rules`; dbt owns everything in `dbt/models`
and only *reads* the Python tables (declared as sources).

### Data flow: ingestion → processing → storage → analytics → frontend

1. **Ingestion.** `collect.py` asks BarentsWatch for each ship's latest message
   every 10 minutes for 330 minutes (33 polls). A ship that hasn't transmitted
   since the last poll comes back unchanged; those repeats are dropped in
   memory using the key `(mmsi, msgtime)`. At the end, all polls are written to
   a local Parquet file and loaded into Bronze with **one** `INSERT ... SELECT
   FROM read_parquet(...)`. Each poll keeps its own `received_at`.
2. **Processing, Silver.** `silver_stage_new_batch.sql` takes Bronze rows newer
   than the newest `received_at` already in Silver or Quarantine, ranks
   duplicates, and assigns each row either `NULL` (accepted) or a rejection
   reason. Two tiny SQL files then route rows to Silver or Quarantine.
   `transform.py` checks that Silver + Quarantine = Bronze.
3. **Processing, states.** For each ship with new Silver rows, the state machine
   rebuilds only the tail of its history (see §3) into `ship_state_periods`.
4. **Processing, visits.** For each ship whose states changed, `port_calls.py`
   groups states into visits in `port_call_events`.
5. **Storage / analytics, Gold.** dbt builds `stg_ports` (parses coordinates),
   `stg_port_calls` (adds the stop position from Silver), then `dim_port`,
   `dim_vessel`, `dim_date`, `fct_vessel_track` and the incremental
   `fact_port_call` (nearest port, `visit_type`), and runs 32 tests.
6. **Frontend.** Four data loaders query Gold and write `port_calls.csv`,
   `ships.csv`, `tracks.parquet` and `freshness.json`. Observable Framework
   bundles them with the page, and wrangler uploads the site. The browser does
   all filtering, drawing and the replay locally.

### Folder structure

```
HarbourOS/
├── src/HarbourOS/          Python package (the pipeline)
│   ├── ingestion.py        BarentsWatch auth + fetch (live and 24-h historic)
│   ├── collect.py          10-minute polling loop, repeat removal, single upload
│   ├── storage.py          warehouse connection and every write
│   ├── transform.py        Silver runner, incremental state and port-call rebuilds
│   ├── state_machine.py    readings → state periods, confidence per reading
│   ├── port_calls.py       state periods → visits, completeness, RULES_VERSION
│   ├── audit.py            read-only data-quality report
│   └── orchestration.py    Dagster assets (local runs only)
├── sql/                    Silver staging and routing SQL
├── dbt/
│   ├── models/staging/     stg_ports (coordinate parsing), stg_port_calls (stop position)
│   ├── models/marts/       dim_port, dim_vessel, dim_date, fact_port_call, fct_vessel_track
│   ├── seeds/              un_locode_ports.csv (610 NO + SJ seaports)
│   └── tests/              4 singular tests
├── dashboard/
│   ├── src/index.md        the whole page
│   ├── src/data/*.py       build-time data loaders
│   ├── src/style.css       palette and layout
│   └── scripts/screenshots.mjs   headless screenshots for previews
├── scripts/                one-off tools (see §12)
├── tests/                  64 pytest tests
├── docs/                   this file
└── .github/workflows/      ci.yml, pipeline.yml, preview.yml, audit.yml
```

### The most important files, in the order to explain them

1. `src/HarbourOS/collect.py`: why data arrives every 10 minutes and uploads once.
2. `sql/silver_stage_new_batch.sql`: the single source of truth for data validity.
3. `src/HarbourOS/state_machine.py`: the core idea (speed first, confidence).
4. `src/HarbourOS/port_calls.py`: what a "visit" is, and completeness.
5. `src/HarbourOS/transform.py`: incremental per-ship rebuilds and invariants.
6. `dbt/models/marts/fact_port_call.sql`: port matching and incremental Gold.
7. `.github/workflows/pipeline.yml`: how production actually runs.

---

## 3. Data engineering implementation

### 3.1 Data source

| Question | Answer |
|---|---|
| Source | **BarentsWatch** (the Norwegian Coastal Administration's open data service), AIS API |
| Endpoint used | `https://live.ais.barentswatch.no/v1/latest/combined?modelType=Full&modelFormat=Json`: each ship's latest message, as JSON, for the Norwegian area. The default "Simple" model has only position, speed, course, heading, rate of turn, name and ship type; "Full" adds navigational status and the voyage fields. The pipeline asked for the default until 2026-10-02 (see below) |
| Auth | OAuth2 client credentials at `https://id.barentswatch.no/connect/token`, scope `ais` |
| Type | Semi-structured JSON, one object per ship: `mmsi`, `name`, `latitude`, `longitude`, `speedOverGround`, `courseOverGround`, `trueHeading`, `rateOfTurn`, `shipType`, `navigationalStatus`, `stream`, `msgtime` (the columns kept in Bronze) |
| Voyage fields | `destination`, `eta`, `imoNumber`, `callSign`: typed in by crews, stored raw in Bronze and carried to Silver (`destination`, `eta`, `imo_number`, `call_sign`) from 2026-10-01, but run #115 (2026-10-02) logged `destination 0/115529`: the pipeline was asking for the "Simple" model, which leaves these fields out. Collection really starts with the fix that asks for "Full". Each collect step logs how many messages carried each field, navigational status included. Nothing uses them yet; they feed the destination-resolution plan (§9) |
| Ships per poll | ~4,142–4,147 (pipeline run #94, 2026-09-29/30). The ingestion docstring records 4,014 at an earlier date |
| New messages per poll | ~3,400 after removing repeats (run #94: 3,393–3,449 for polls 2–33) |
| Messages per run | 113,147 stored from 33 polls (run #94) |
| Collection method | Micro-batch polling every 10 minutes inside a ~5.5-hour GitHub Actions job; ~4 runs a day |
| Historic data | The live endpoint has **no history**. BarentsWatch's historic endpoint (`/v1/historic/trackslast24hours/{mmsi}`) gives 24 hours per ship; it was used **once** (2026-09-05, `scripts/backfill_historic.py`) for ships that had moved faster than 5 knots, taking Bronze from 283 to ~28,000 rows |
| When data starts | First Bronze data 2026-08-30 (Day 2 commit). Continuous 10-minute collection from 2026-09-26; gap-free chained runs from 2026-09-28 |
| How much is stored (2026-09-30 02:02 UTC, run #94 log) | Bronze **1,884,987** rows; Silver **1,791,808**; Quarantine **93,179**; state periods **157,036**; port-call events **20,365** |
| Distinct ships | 3,651 ships had new data in run #94. The dashboard notes about 6,000 ships in total (2026-09-29, approximate). The exact current `dim_vessel` count is **not recorded** |
| Warehouse size | 157 MB (`PRAGMA database_size`, 2026-09-29). MotherDuck billed storage reached 2.5 GB during hourly runs, 2.4 GB of it "failsafe" copies (2026-09-29) |
| Retention | **Nothing is deleted.** Bronze, Silver and Quarantine keep everything. Only the *views* are windowed: `fct_vessel_track` holds the last 7 days, the dashboard's route file the last 3 days, and the charts the last 7 days |
| Days of data stored | About 31 calendar days (2026-08-30 → 2026-09-30), but dense only since 2026-09-26. There is **no retention policy**, so storage grows without limit (see §8) |
| How live ingestion works | See §2 step 1 and §5 "Near-real-time" |

**Not recorded:** the total size of the raw JSON received, the exact date of
the first live poll with all ships, and the BarentsWatch rate limits.

### 3.2 Data challenges

| Challenge | Evidence | Where handled |
|---|---|---|
| **Repeated messages.** The "latest" endpoint returns the same message for a ship that hasn't transmitted | Poll 1 returns 4,142 new, later polls ~3,400 of ~4,145 | Dropped in `collect.py` before storage; any that slip through are quarantined as `duplicate` |
| **Duplicates** | 86,611 of 93,179 quarantined rows are `duplicate` (run #94) | `ROW_NUMBER() OVER (PARTITION BY mmsi, msgtime)` plus a check against earlier Silver rows |
| **Missing values** | 5,680 rows `missing_speed` (run #94) | Quarantined; the speed-first state machine can't use them |
| **Invalid identifiers** | 731 rows `invalid_mmsi_range` (MMSI not 9 digits) | Quarantined |
| **Impossible values** | 157 rows `invalid_speed` (outside 0–102.2 knots; 102.3 is AIS's "not available" code) | Quarantined |
| **NULLs silently vanishing** | An early bug: rows with NULL latitude, longitude or speed were in neither Silver nor Quarantine, because a comparison with NULL is "unknown" (commit 27cef53) | Explicit `IS NULL` checks, and the Bronze = Silver + Quarantine assertion |
| **Crew-typed status is wrong** | A ship "moored" at 7 knots for 11 hours | Speed decides the state; status only adjusts confidence |
| **GPS jitter** | Short flickers between stopped and moving | 3-reading dwell before a new state is believed |
| **Gaps in sightings** | Before 2026-09-26, once-a-run snapshots hours apart; on 2026-09-28 a skipped trigger left a ~2-hour gap | 30-minute `MAX_GAP`: a gap ends a period and a visit; collection was redesigned (PR #4, PR #11) |
| **Port positions in an odd format** | UN/LOCODE stores `6753N 01259E` as degrees and *minutes* | Parsed once in `stg_ports.sql`, guarded by a singular test |
| **Port list gaps** | Svalbard (country code SJ) was missing; ~1,626 stops lay 10–25 km from any listed port, likely quays UN/LOCODE doesn't list (audit 2026-09-28) | Svalbard added (PR #6); far stops labelled `at_sea` (PR #7); missing quays are still open |
| **Volume vs network latency** | 4,284 row-by-row inserts took 20 minutes against MotherDuck; per-ship queries took 75 minutes (4 seconds locally) | Chunked inserts, single-query fetches, Parquet staging (§6) |
| **Schema change on an incremental table** | Adding `visit_type` broke dbt: "Referenced column not found" | `on_schema_change='fail'` plus automatic full refresh (§7) |

**Not implemented:** checks for positions outside Norwegian waters, `0,0`
positions or "teleporting" ships are *reported* by the audit but not
filtered in Silver. The Silver rules only reject values that are impossible
anywhere.

### 3.3 Cleaning and transformation

**How raw data is cleaned.** One CASE expression in
`sql/silver_stage_new_batch.sql` checks, in order: duplicate in this batch,
duplicate of an earlier batch, missing or out-of-range MMSI, missing or
invalid latitude, longitude and speed, missing timestamp, and timestamps in
the future. The first failing rule wins and becomes the `rejection_reason`.
Accepted rows are renamed to snake_case in Silver.

**How invalid records are handled.** They are never deleted. They go to
`ais_messages_quarantine` exactly as received, with the reason attached, so
the audit can report them and nothing disappears unexplained.

**How duplicates are removed.** Twice:

1. In memory during collection: `(mmsi, msgtime)` seen before in this run is
   skipped.
2. In SQL: within a batch, the earliest `received_at` wins (with a
   deterministic tie-break on the row's values); across batches, a row whose
   `(mmsi, message_time)` already exists in Silver is a duplicate.

**Manually entered / crew-generated errors.** Honest answer: the only
crew-typed fields HarbourOS uses are **navigational status**, **ship name** and
**ship type**.

- *Navigational status* is the one handled seriously: it is never trusted to
  decide the state, only to score confidence (agree 1.0, no signal 0.5,
  contradict 0.3). Until 2026-10-02 the live feed was requested in its
  "Simple" form, which has no status, so production stops most likely all
  scored "no signal" *(inferred from the API's model definitions; not
  measured in the warehouse)*.
- *Ship name and type:* `dim_vessel` takes the **most recent** value
  (`arg_max(name, message_time)`), because crews re-broadcast and correct
  them. There is **no** spelling correction or name matching.
- *Destination text* ("BGO", "BERGN", "FOR ORDERS") is only now being
  collected (see the voyage fields above), so no destination spelling
  problems have been seen or fixed yet. Resolving it is
  the top AI roadmap item (§9).

**Standardisation and entity matching that *is* implemented:**

- **Spatial entity matching:** each stop is matched to the nearest UN/LOCODE
  seaport by great-circle distance, within 10 km (`port_match_km` dbt
  variable). The distance is kept (`nearest_port_km`) so a match can be
  judged.
- **Ship type grouping:** the numeric ITU-R M.1371 type is mapped to
  categories (cargo, tanker, passenger, fishing, ...) in both `dim_vessel` and
  the audit.
- **Units and formats:** coordinates converted to decimal degrees; durations
  stored as whole minutes; dashboard times shown in Europe/Oslo.

**How accuracy of the final output was improved** (each change came from
disbelieving an output):

1. A visit ends only when the ship returns to sea, not when it drifts on its
   mooring lines (Day 5: first output had 408 events, ~6 per ship per day).
2. A gap longer than 30 minutes ends a visit: "a blind spot is not proof the
   ship stayed put".
3. Visits with fewer than 3 readings are dropped.
4. Stops more than 10 km from a port are `at_sea`, not port calls (PR #7).
   Stops within 300 m of a fish farm are `fish_farm` (2026-10-01): the
   reliability check found service boats at farms counted as port calls.
   A port call must also be within 500 m of an official berth in Kystverket's
   location register (2026-10-01); other stops near a port, and stops at
   official anchorages, are `anchorage`. The check had found many "port
   calls" were ships waiting off the coast. On a day of data, 77% of stops
   within 500 m of an official berth were also beside a quay on
   OpenStreetMap, against 20% of the rest.
5. Svalbard ports added (PR #6).
6. A neighbouring state only counts as witnessing arrival or departure if it
   joins without a gap (PR #15; this removed 1,566 false "complete" ~38-hour
   stays).
7. The stay-length chart uses only fully observed visits.
8. "In port now" requires a sighting within 6 hours of the freshest data.

---

## 4. Database and storage

### Technologies and why

- **DuckDB** (embedded, columnar, analytical SQL). Tests run against a
  temporary file in milliseconds, and the same SQL runs in production.
- **MotherDuck** (hosted DuckDB). The GitHub Actions runner has no persistent
  disk, so the warehouse has to live somewhere; MotherDuck keeps the same
  engine and SQL. Free plan limits that shaped the design: 10 compute hours a
  month and 10 GB of storage.
- **Parquet** for two things: staging each run's upload, and the dashboard's
  route file (smaller than CSV, typed, compressed with zstd).
- **CSV / JSON** for the smaller dashboard files.
- **dbt seed** (CSV in git) for the port list, so the project builds from a
  fresh clone.

### Layers (medallion)

| Layer | Tables | Written by |
|---|---|---|
| Bronze | `ais_messages_bronze` | `storage.insert_ais_snapshots` |
| Silver | `ais_messages_silver`, `ais_messages_quarantine` | `sql/` via `transform.py` |
| Derived (between Silver and Gold) | `ship_state_periods`, `port_call_events`, `derived_progress`, `derived_rules` | `transform.py` |
| Gold | `dim_port`, `dim_vessel`, `dim_date`, `fact_port_call`, `fct_vessel_track` (+ views `stg_ports`, `stg_port_calls`, seed `un_locode_ports`) | dbt |

All tables live in one database, schema `main`.

### Table schemas

**`ais_messages_bronze`** (raw, as received; `storage.py`)

| Column | Type | Meaning |
|---|---|---|
| mmsi | INTEGER | Ship identifier (Maritime Mobile Service Identity) |
| name | VARCHAR | Ship name as broadcast |
| latitude, longitude | DECIMAL(10,6) | Position |
| speedOverGround | DECIMAL(10,2) | Knots, GPS-measured |
| courseOverGround | DECIMAL(10,2) | Degrees |
| trueHeading | DECIMAL(10,2) | Degrees |
| rateOfTurn | DECIMAL(10,2) | As reported |
| shipType | INTEGER | ITU-R M.1371 code |
| navigationalStatus | INTEGER | Crew-set status (0 under way, 1 anchored, 5 moored, 8 sailing, ...) |
| stream | VARCHAR | As delivered by the API (**meaning not documented in the repo**) |
| msgtime | TIMESTAMP | When the ship sent it |
| received_at | TIMESTAMP | When this poll ran; one value per poll, used as the batch id |

**`ais_messages_silver`**: the same columns renamed to snake_case
(`speed_over_ground`, `navigational_status`, `message_time`, ...). Created with
`CREATE TABLE ... AS SELECT ... FROM bronze WHERE FALSE`, so its shape can
never disagree with the query that fills it.

**`ais_messages_quarantine`**: every Bronze column plus
`rejection_reason VARCHAR`.

**`ship_state_periods`**: `mmsi INTEGER`, `state VARCHAR` (at_sea, approach,
berthed, anchored, departed, unknown), `start_time`, `end_time` TIMESTAMP,
`n_readings INTEGER`, `confidence DECIMAL(3,2)`, `note VARCHAR` (the reason for
the lowest confidence seen, e.g. `status_claims_underway_but_stopped`).

**`port_call_events`**: `mmsi`, `stop_type` (berthed/anchored), `arrival_time`,
`berth_start`, `berth_end`, `departure_time`, `minutes_alongside INTEGER`,
`n_readings`, `confidence DECIMAL(3,2)`, `completeness` (complete,
arrival_unobserved, departure_unobserved, both_unobserved).

**`derived_progress`**: `layer VARCHAR`, `mmsi INTEGER`,
`built_from_received_at TIMESTAMP`. The per-ship watermark for the
`state_periods` and `port_calls` layers.

**`derived_rules`**: `layer VARCHAR`, `version INTEGER`. The rules version a
layer was last built with.

**`dim_port`** (grain: one seaport): `port_locode` (PK, e.g. NOBGO),
`port_name`, `subdivision`, `locode_status`, `latitude`, `longitude` (decimal
degrees).

**`dim_vessel`** (grain: one MMSI): `mmsi` (PK), `vessel_name`, `ship_type`
(latest reported), `first_seen`, `last_seen`, `n_readings`, `ship_category`.

**`dim_date`** (grain: one day observed): `date_key` (PK, YYYYMMDD int),
`date_day`, `calendar_year`, `calendar_month`, `calendar_day`, `day_name`,
`is_weekend`.

**`fact_port_call`** (grain: one ship stopping once): `port_call_key` (PK, md5
of mmsi and berth_start), `mmsi` (FK dim_vessel), `port_locode` (FK dim_port,
NULL unless a port call), `visit_type` (port_call/anchorage/at_sea/fish_farm),
`fish_farm_name`, `berth_name`, `berth_m`, `anchorage_name`, `nearest_port_km`,
`arrival_date_key` (FK dim_date), `stop_type`, `completeness`, `arrival_time`,
`berth_start`, `berth_end`, `departure_time`, `minutes_alongside`,
`n_readings`, `confidence`, `stop_latitude`, `stop_longitude`,
`built_from_received_at`.

**`fct_vessel_track`** (grain: one ship in one 10-minute slot, last 7 days):
`mmsi` (FK dim_vessel), `slot_start`, `message_time`, `latitude`, `longitude`,
`speed_over_ground`, `course_over_ground`, `true_heading`,
`navigational_status`.

### Relationships

```
dim_vessel (mmsi) ─┬─< fact_port_call >── dim_port (port_locode)
                   │         │
                   │         └──> dim_date (arrival_date_key)
                   └─< fct_vessel_track
```

These are enforced by dbt `relationships` tests, not by database foreign keys.

### Partitioning and indexing

- **No partitioning and no indexes** are declared. DuckDB/MotherDuck store
  data in columnar row groups with automatic min/max statistics (zone maps),
  which lets filters on time skip data; nothing in the project configures
  this explicitly.
- The *logical* partition key throughout is **`mmsi`**: the state machine,
  the incremental rebuilds and the fact table's `delete+insert` all work one
  ship at a time, because a ship's states depend only on its own readings.

### Retention

As in §3.1: raw layers keep everything; `fct_vessel_track` is a rolling
7-day window measured from the newest reading (so a paused pipeline still
shows its last week); the dashboard files are 3–7 days. There is no archive
or purge job.

### Tests on the data (dbt)

32 data tests pass on every run (run #94: `PASS=40`, which is 7 models, 1 seed
and 32 tests). Generic: `unique`/`not_null` on keys, `relationships`
between facts and dimensions, `accepted_values` on `visit_type`, `stop_type`,
`completeness` and `ship_category`. Singular tests:

- `assert_coordinates_are_degrees_and_minutes`: every parsed port sits on an
  exact arc-minute.
- `assert_ports_are_in_norway`: every port is inside mainland Norway's or
  Svalbard's bounding box.
- `assert_visit_type_matches_port_locode`: a `port_call` always has a port and
  an `anchorage`, `at_sea` or `fish_farm` stop never does.
- `assert_track_has_one_row_per_ship_and_slot`: the track grain holds.

### Important SQL, explained

**1. Incremental Silver with a derived watermark** (`sql/silver_stage_new_batch.sql`)

```sql
WITH already_processed AS (
    SELECT COALESCE(max(received_at), TIMESTAMP '1970-01-01') AS up_to
    FROM (SELECT received_at FROM ais_messages_silver
          UNION ALL SELECT received_at FROM ais_messages_quarantine)
),
new_rows AS (
    SELECT b.* FROM ais_messages_bronze b, already_processed p
    WHERE b.received_at > p.up_to
),
ranked AS (
    SELECT n.*, ROW_NUMBER() OVER (
        PARTITION BY mmsi, msgtime
        ORDER BY received_at, concat_ws('|', latitude, longitude, ...)) AS row_num
    FROM new_rows n
)
SELECT r.* EXCLUDE (row_num),
       CASE WHEN r.row_num != 1 THEN 'duplicate'
            WHEN EXISTS (SELECT 1 FROM ais_messages_silver s
                         WHERE s.mmsi = r.mmsi AND s.message_time = r.msgtime) THEN 'duplicate'
            WHEN r.mmsi IS NULL THEN 'missing_mmsi'
            ...
            ELSE NULL END AS rejection_reason
FROM ranked r;
```

- *Why:* process only new Bronze rows, and decide accept/reject in one place.
- *Problem solved:* Silver and Quarantine can't drift apart, and no
  bookkeeping table is needed, because every Bronze row lands in exactly one
  of them, so their newest `received_at` *is* the watermark.
- *Optimisation:* the work per run is proportional to new rows. The
  tie-break makes deduplication deterministic, so re-running gives the same
  answer.

**2. Rebuild only each ship's tail** (`transform.py`, `state_period_restarts`)

```sql
WITH new_readings AS (          -- ships with Silver rows newer than their watermark
    SELECT s.mmsi, max(s.received_at) AS newest_reading, min(s.message_time) AS earliest_new
    FROM ais_messages_silver s
    LEFT JOIN derived_progress p ON p.mmsi = s.mmsi AND p.layer = 'state_periods'
    WHERE p.built_from_received_at IS NULL OR s.received_at > p.built_from_received_at
    GROUP BY s.mmsi
),
restarts AS (                   -- latest believable period at or before the new data
    SELECT p.mmsi, max(p.start_time) AS since,
           arg_max(p.previous_state, p.start_time) AS previous_state
    FROM periods p JOIN new_readings n USING (mmsi)
    WHERE p.n_readings >= 3 AND p.start_time <= n.earliest_new
    GROUP BY p.mmsi
)
SELECT n.mmsi, n.newest_reading,
       coalesce(r.since, TIMESTAMP '1970-01-01') AS since, r.previous_state
FROM new_readings n LEFT JOIN restarts r USING (mmsi);
```

- *Why:* the state machine reads left to right, and nothing after a believable
  period can reach back past it. So rebuilding from that point gives exactly
  what a full rebuild would.
- *Problem solved:* without it, every run would reprocess all history (~1.8M
  readings). Run #94 read 519,058 readings for 3,651 ships instead.
- *Optimisation:* all stale ships' readings are then fetched in **one**
  query (`JOIN state_period_restarts ... ORDER BY mmsi, message_time`) and
  grouped in Python, instead of one query per ship. A randomised test checks
  that batch-by-batch rebuilding equals one full rebuild.

**3. Nearest port, incremental** (`dbt/models/marts/fact_port_call.sql`)

```sql
{{ config(materialized='incremental', incremental_strategy='delete+insert',
          unique_key='mmsi', on_schema_change='fail') }}
with calls as (
    select * from {{ ref('stg_port_calls') }}
    {% if is_incremental() %}
    where built_from_received_at > (select coalesce(max(built_from_received_at), timestamp '1970-01-01')
                                    from {{ this }})
    {% endif %}
),
distances as (
    select calls.mmsi, calls.berth_start, ports.port_locode,
           6371 * 2 * asin(sqrt(
               pow(sin(radians(ports.latitude - calls.stop_latitude) / 2), 2)
               + cos(radians(calls.stop_latitude)) * cos(radians(ports.latitude))
               * pow(sin(radians(ports.longitude - calls.stop_longitude) / 2), 2))) as distance_km
    from calls cross join {{ ref('dim_port') }} as ports
),
nearest as (
    select * from distances
    qualify row_number() over (partition by mmsi, berth_start order by distance_km) = 1
)
select ..., case when nearest.distance_km <= {{ var('port_match_km', 10) }}
                 then nearest.port_locode end as port_locode, ...
```

- *Why:* every stop is measured against all 610 seaports (a cross join), which
  would grow with total history if rebuilt every run.
- *Problem solved:* matches stops to ports honestly: no port beyond 10 km, and
  the distance kept for judgement.
- *Optimisation:* incremental on ships whose visits changed. Deletion is keyed
  on `mmsi`, not on the visit, because a ship's visits are recomputed as a set;
  if two visits merge, keying on the visit would leave a stale row behind.
  `QUALIFY` picks the nearest port without a self-join.

**4. Parsing UN/LOCODE coordinates** (`stg_ports.sql`)

```sql
(lat_degrees + lat_minutes / 60.0) * case when lat_hemisphere = 'S' then -1 else 1 end as latitude
```

- *Why / problem:* "6753N" is 67°53′ = 67.88°, not 67.53°. Reading it as a
  decimal moves every port by up to 35 km, quietly and plausibly. Parsed once
  in staging, and a test proves every result lands on a whole arc-minute.

**5. Latest name and type per ship** (`dim_vessel.sql`)

```sql
select mmsi, arg_max(name, message_time) as vessel_name,
       arg_max(ship_type, message_time) as ship_type, ...
from ais_messages_silver group by mmsi
```

- *Why:* crews correct names and types; the newest value is the best one.
  `arg_max` avoids a window function plus a filter.

**6. One point per ship per 10 minutes** (`fct_vessel_track.sql`)

```sql
select mmsi, time_bucket(interval 10 minutes, message_time) as slot_start,
       arg_max(latitude, message_time) as latitude, ...
from recent group by mmsi, slot_start
```

- *Why:* routes for the map at a fixed resolution; the window is measured from
  the newest reading, not the clock, so a paused pipeline still shows a week.
- *Optimisation:* it reads only 7 days of Silver, so its cost stays flat.

**7. Thinning routes for the browser** (`dashboard/src/data/tracks.parquet.py`)

Uses `lag`/`lead` over each ship's track and keeps a point only if it is the
first or last, borders a gap over 30 minutes, or moved more than 0.15 km.
Result (run #94): **290,267 points in 3.1 MB** of Parquet for 3 days of
routes.

**8. "In port now"** (`port_calls.csv.py`, `ships.csv.py`)

```sql
f.completeness in ('departure_unobserved', 'both_unobserved')
  and f.berth_end >= freshness.latest_reading - interval '6 hours' as in_port_now
```

- *Why:* a ship that stopped transmitting weeks ago would otherwise be "in
  port" forever. Computed at build time rather than stored in the incremental
  fact table, which only updates ships with new data and would go stale in the
  same way.

**9. Gap distribution** (`audit.py`, "Time between consecutive sightings")

Uses `lag(message_time) over (partition by mmsi order by message_time)` to
bucket the time between sightings. This is the query that proved GitHub's
cron was skipping runs (4% of gaps were 2–12 hours on 2026-09-28).

---

## 5. Data engineering concepts, where they live

| Concept | Implemented? | Where and how |
|---|---|---|
| **Batch processing** | Yes | Every pipeline run is a batch: 5.5 hours of polls uploaded once, then Silver, states, port calls, dbt and the site built once (`pipeline.yml`) |
| **Micro-batch ingestion** | Yes | 33 polls, 10 minutes apart, per run (`collect.py`) |
| **Incremental processing** | Yes, at every layer | Silver: derived `received_at` watermark. States: per-ship watermark in `derived_progress` + tail restart. Port calls: ships whose state watermark moved. Gold: dbt incremental `delete+insert` on `mmsi`. Track: fixed 7-day window |
| **Streaming / near-real-time** | **No.** Near-real-time only in the sense of 10-minute sampling | Data reaches the site every ~5.5 hours. There is no stream consumer or message queue. (An earlier `CLAUDE.md` called it "real-time streaming" and "Server-Sent Events"; that was wrong and is corrected) |
| **ETL vs ELT** | Mostly **ELT** | Raw data is loaded to Bronze first, then transformed inside the warehouse (SQL, dbt). The state machine is the "T outside the warehouse" exception: it extracts Silver, transforms in Python, and loads back |
| **Data validation** | Yes | Silver rules (`sql/`), Pydantic is a dependency but **not used** in the code |
| **Data quality checks** | Yes | 32 dbt tests every run; row-accounting assertions in `transform.py` (Silver + Quarantine = Bronze; state periods cover every Silver reading); the 14-check audit report (`audit.py`, manual workflow) |
| **Idempotency** | Yes, for the transforms | Silver's watermark and duplicate rules make re-running a no-op. States and port calls use delete-then-insert per ship. `port_call_key` is a deterministic md5. **Caveat:** the Bronze upload itself is append-only; re-uploading the same messages would add rows, which Silver then quarantines as duplicates |
| **Error handling** | Yes | A failed poll is logged and skipped without losing earlier polls. A run with zero ships fails on purpose. Each audit check reports its own failure without hiding the others. dbt schema drift triggers a full refresh. The site is only deployed if every step passed |
| **Retry mechanisms** | Partial | No retry library. A failed poll is effectively retried by the next poll 10 minutes later. The next run starts even after a failure. A 6-hourly cron restarts the chain if it breaks. The preview's screenshot push retries 3 times |
| **Logging** | Basic | `print` statements to the GitHub Actions log (poll counts, row counts, quarantine breakdown, rebuild counts). No structured logging or log storage |
| **Monitoring and alerting** | Yes, simple | "Pipeline is failing" issue assigned to Maria (GitHub emails her), closed automatically on success. The site shows "Data last updated" and warns after 7 hours. Preview runs save screenshots and a page-error report |
| **Pipeline orchestration** | Yes | Production: one GitHub Actions job with ordered steps, a concurrency group so two runs never write at once, and self-chaining via `workflow_dispatch`. Local: Dagster assets with declared dependencies and a 15-minute schedule |
| **Schema evolution** | Yes | `on_schema_change='fail'` + automatic `--full-refresh` in `pipeline.yml` |
| **Rules versioning / backfill** | Yes | `port_calls.RULES_VERSION`: when it changes, every ship is re-derived and the fact table fully refreshed, automatically |
| **CI/CD** | Yes | `ci.yml` (ruff, black, mypy, pytest) on PRs; the pipeline deploys the site; previews for `dashboard-*` branches |
| **Testing** | Yes | 64 pytest tests (83% line coverage) using temporary DuckDB files and injectable clocks/fetchers, e.g. a fake clock for the collector |

---

## 6. Performance optimisation

| What | Before | After | Why it works |
|---|---|---|---|
| **Bulk inserts** (`_bulk_insert`) | `executemany`: 4,284 rows took **20 minutes** against MotherDuck | 500 rows per INSERT statement | Each statement is a network round trip; row-by-row was thousands of trips |
| **One query instead of one per ship** | Per-ship queries: **75 minutes** on MotherDuck (4 seconds locally) | One query, grouped in Python | Same code, only the distance to the database changed |
| Both together (commit d5a3d12) | ~17,000 round trips per run | ~40 | |
| **Upload once per run** (`insert_ais_snapshots`) | One upload per poll would mean 33 per run | Polls staged locally in Parquet, one `INSERT ... SELECT FROM read_parquet` | Keeps warehouse work flat however often we poll; protects the 10 compute-hour free allowance |
| **Drop repeats before storing** | Every poll would store ~4,100 rows | ~3,400 new rows per poll kept | Less storage, less Quarantine noise |
| **Incremental Silver** | Full re-validation each run | Only rows past the watermark | Cost grows with new data, not history |
| **Tail rebuild of states** | Rebuild every ship from scratch | Only ships with new data, from their last believable period | Run #94 read 519k of 1.79M readings |
| **Incremental fact table** | Cross join of every visit × 610 ports each run | Only changed ships | The cross join is the expensive part |
| **Windowed track table** | | Last 7 days only | Flat cost |
| **Run timings today** (run #94) | | Transform 30 s, dbt 24 s (15 s of model time), dashboard build 23 s, deploy 15 s | The collection wait is the only long step |
| **Route file** | | 3 days, points kept only when a ship moved >150 m, rounded to ~10 m, whole seconds, zstd Parquet: 290k points, 3.1 MB | The page downloads it whole |
| **Parquet over CSV** for routes | | Several times smaller, typed columns | |
| **Page weight** (run #94 build) | | Page 57 kB, imports 1.95 MB, data files 7.5 MB | Mostly MapLibre and the data |
| **Browser lookups** | | Each ship's slice of the route columns is indexed once; the replay finds positions with a binary search | Scrubbing the replay stays smooth |
| **Map resilience** | | Base-map tiles time out after 8 s and ships draw on plain water; without WebGL the rest of the page still works | Slow or blocked tile servers don't break the page |
| **Static site, no backend** | | Nothing queries the warehouse at view time | Free, fast, and no database exposed |
| **Caching** | | uv and npm caches in Actions; Observable's local loader cache; Cloudflare only uploads changed files (run #94: 5 uploaded, 53 already there) | |
| **Previews skip the warehouse** | Previews rebuilt dbt | Previews read master's Gold unless the branch changes `dbt/` | No writes colliding with the live pipeline, less compute |
| **Storage** | Hourly runs: 2.5 GB in 1.5 days (mostly MotherDuck "failsafe" copies of rewritten tables) | Back to ~4 runs a day (PR #18) | Every run rewrites some tables; fewer runs, fewer copies |

**Not measured:** page load time in the browser, and MotherDuck compute per run
(the free plan doesn't expose query history; it can only be read on the
MotherDuck billing page).

---

## 7. Problems faced during development

Real problems, in roughly the order they happened.

1. **NULL rows vanished from Silver** (2026-09-05). Rows with a NULL latitude,
   longitude or speed were in neither Silver nor Quarantine, because `NULL
   BETWEEN a AND b` is "unknown", not false. *Fix:* explicit `IS NULL` rules
   and an assertion that Silver + Quarantine = Bronze. *Lesson:* count your
   rows at every boundary; three-valued logic bites silently.

2. **Not enough data to see a journey** (2026-09-05). One snapshot per run gave
   283 rows. *Fix:* one-off 24-hour historic backfill (~28k rows).
   *Lesson:* check the sampling rate against the question you're asking.

3. **The first port calls were unbelievable** (2026-09-06). 408 events, about
   6 port calls per ship per day, one lasting 6.5 days inside a 24-hour window.
   *Fixes:* a visit ends only when the ship goes back to sea; stray pings days
   apart never merge; losing sight ends a visit; fewer than 3 readings is not
   a visit. Result: 163 events. *Lesson:* sanity-check outputs against what's
   physically possible.

4. **UN/LOCODE coordinates are degrees and minutes** (2026-09-07). Misreading
   them moves ports up to 35 km. *Fix:* parse once, test that every result is
   on an arc-minute. *Lesson:* test the riskiest line directly.

5. **dagster-dbt broke on Python 3.14** (2026-09-08). *Fix:* run dbt as a
   subprocess and keep modern Python. *Trade-off:* no per-model lineage in
   Dagster.

6. **Evidence dropped static export** (2026-09-10). *Fix:* moved to Observable
   Framework. *Lesson:* prefer tools whose output outlives them.

7. **Each ingest dropped the Bronze table** (fixed 2026-09-10, commit f05c5d7).
   Ingestion ran `DROP TABLE IF EXISTS ais_messages_bronze`, so history didn't
   accumulate. *Fix:* `CREATE TABLE IF NOT EXISTS` and batched inserts.

8. **Only 100 of ~4,000 ships** (fixed 2026-09-14, and again in Dagster on
   2026-09-28). `fetch_ais_data(limit=100)` was a development cap that stayed.
   *Lesson:* a hard-coded cap silently discards data as the source grows; the
   docstring now says so.

9. **The cloud was 1,000× slower for the same code** (2026-09-16 → 09-18). 20
   minutes for 4,284 inserts, 75 minutes for per-ship queries. *Fix:* chunked
   inserts, single-query fetches (~17,000 → ~40 round trips). *Lesson:* design
   for the network, not the laptop.

10. **GitHub Actions env names are case-insensitive** (2026-09-16).
    `MOTHERDUCK_TOKEN` and `motherduck_token` can't both be defined. *Fix:*
    define only the lowercase one, and have `connect()` accept either.

11. **"Hourly" cron wasn't hourly** (found 2026-09-26). Runs started 3–6 hours
    apart, and one snapshot per run meant sightings were never within the
    30-minute gap, so visits couldn't be stitched. *Fix:* collect every 10
    minutes inside a long run (PR #4), then each run starts the next (PR #11)
    with a 6-hourly backstop. Port calls went from ~150 to 2,624 on the first
    preview. *Lesson:* don't build correctness on a scheduler's promise.

12. **Svalbard stops looked 800 km out at sea** (2026-09-27). UN/LOCODE files
    Svalbard under SJ, not NO. *Fix:* include SJ (PR #6).

13. **Oil rigs counted as port calls** (2026-09-27). 14% of stops were over
    10 km from any port. *Fix:* `visit_type = 'at_sea'` (PR #7).

14. **"Cancelled" runs that weren't failures** (2026-09-27). GitHub's
    concurrency group keeps one queued run; a newer trigger replaces it.
    *Fix:* none needed in code; documented.

15. **A new column broke every run** (2026-09-27/28, runs #38–#41). dbt's
    incremental model ignores new columns by default, and the one-off full
    refresh was cancelled in the queue, so `visit_type` never reached
    MotherDuck: `Binder Error: Referenced column "visit_type" not found`.
    *Fix:* `on_schema_change='fail'` and an automatic `--full-refresh` when dbt
    reports the schema is out of sync (PR #9). *Lesson:* incremental models
    need a schema-change strategy.

16. **A two-hour hole in collection** (2026-09-28). An hourly trigger simply
    didn't fire after run #45. *Fix:* self-chaining runs (PR #11).

17. **False 38-hour stays** (2026-09-28). When collection paused at 06:04,
    1,566 visits read as "complete" ~38-hour stays, because any later state,
    even hours after a gap, counted as seeing the departure. *Fix:* a
    neighbour must join within 30 minutes (PR #15), plus `RULES_VERSION` so
    every ship is re-derived automatically. *Lesson:* a manual full refresh on
    a busy queue may never run; make backfills automatic.

18. **MapLibre 6 failed in the build** (2026-09-28). v6 loads its worker as a
    separate file that Framework doesn't copy. *Fix:* MapLibre 5, whose bundle
    includes the worker.

19. **Storage, not compute, hit the free-tier limit** (2026-09-29). Hourly runs
    grew storage to 2.5 GB in 1.5 days, mostly MotherDuck "failsafe" copies.
    *Fix:* back to ~4 runs a day (PR #18). *Lesson:* read the billing model,
    not just the table sizes.

20. **Silent failure modes** (2026-09-29). A hung BarentsWatch connection could
    stall a run until GitHub killed it (and a killed run doesn't start the
    next); an empty API response would republish old data as a "success".
    *Fix:* 60-second request timeout; fail the run if no poll returned ships
    (PR #17).

**Failed or abandoned approaches:** Evidence (static export removed);
dagster-dbt (Python 3.14 incompatibility); one snapshot per run on a cron
(gaps); hourly 50-minute runs (storage); MapLibre 6 (worker loading); relying
on a manual full-refresh run (bumped from the queue).

---

## 8. Current project status

*As of 2026-09-30.*

### Fully completed

- Continuous collection of all ships every 10 minutes, ~4 publishes a day.
- Bronze → Silver with Quarantine and reasons; row accounting enforced.
- Speed-first state machine with confidence; port calls with completeness.
- Incremental processing at every layer; automatic rebuild on rules or schema
  change.
- Gold star schema with 32 data tests, including port matching and `at_sea`.
- Automatic deploy to Cloudflare Pages, failure alerts, freshness indicator.
- Map dashboard: live positions, routes, 24-hour replay, search, charts, Norway
  time, reduced-motion support.
- CI (lint, format, types, 64 tests), dashboard previews with screenshots,
  read-only data audit.
- Automatic reliability check (2026-10-01): every run tests 100 random port
  calls against OpenStreetMap's quays, piers, harbours and ferry terminals,
  and the site shows how many were confirmed (`reliability.py`). A second
  witness is the voyages larger ships report to the authorities (SafeSeaNet),
  read from Kystverket's open Kystdatahuset API: a stop also counts as
  confirmed when the ship reported a voyage to or from a place within 2 km of
  it, within 12 hours. A third witness (2026-10-04) is the destination the
  crew typed into AIS: a stop counts as confirmed when, in the 24 hours before
  or during it, the ship named the stop's port (name or UN/LOCODE) or a
  register place within 2 km. The pipeline never reads the destination to find
  port calls, so it stays independent. The first score was 53/100, and Maria judged it not
  good enough to show, so the tile was hidden. With the stricter
  official-berth rule, run #115 (2026-10-02) scored 77/100, and Maria chose to
  show the tile again.

### Partially completed

- **Dagster:** works for local runs; production uses GitHub Actions.
- **Data audit:** exists and was run on 2026-09-28; the planned re-run around
  2026-09-30 has **not** happened yet.
- **Port list:** Norway and Svalbard only; missing quays not yet added.
- **Visit completeness:** after the stricter rule (PR #15), run #94 reported
  4,127 fully observed visits out of 20,365 (20%). The 2026-09-28 audit's
  78% was measured before that rule change, so the two numbers aren't
  comparable *(inferred)*. Older, sparsely sampled data contributes many
  partly observed visits *(inferred)*.

### Not implemented

- Any AI or machine-learning component (§9).
- Using the collected destination, ETA, IMO number and call sign (stored from 2026-10-01, not yet used).
- True streaming.
- A retention or archiving policy.
- Filters for positions outside Norwegian waters, `0,0` fixes or teleporting
  ships (reported by the audit only).
- Structured logging, metrics dashboards, or automatic retries with back-off.
- Pydantic validation (the library is a dependency but unused).
- Tests for dbt models in pytest (`tests/test_gold.py` is empty; Gold is tested
  by dbt tests instead).

### Current limitations

- ~5.5-hour refresh; faster runs exceed free storage.
- UN/LOCODE marks towns, not quays: matches within 10 km can still be the
  wrong port (e.g. a Nesodden ferry pier not in UN/LOCODE matched to a port
  2.65 km away, noted 2026-09-26).
- Short dense history (since 2026-09-26).
- Unbounded growth of Bronze/Silver; MotherDuck free plan is 10 GB.
- Confidence only reflects status/speed agreement, not position quality or
  match distance.
- GitHub Actions jobs are capped at 6 hours, which sets the 330-minute
  collection length.
- The reliability check confirms place and stillness, not exact times, and
  OpenStreetMap leaves out some small quays, so real port calls can fail it.

---

## 9. AI integration roadmap

**#3 (unusual behaviour) is built (2026-10-04); the rest is not.** This builds on the design written on 2026-09-30
(`ai-plan/ai-design.md` in the project files). Your constraints: free and open
source only, no paid APIs, no chatbot, no text summaries, run inside the
existing GitHub Actions pipeline.

### Prioritised list

| Rank | Idea | Why this rank |
|---|---|---|
| 1 | Destination entity resolution | Solves a real data-quality problem, feeds the confidence score, cheap, measurable |
| 2 | Missing-quay discovery (clustering of unmatched stops) | Uses data you already have, fixes a known accuracy gap, very data-engineering |
| 3 | Unusual-behaviour detection (slim), **built 2026-10-04** | Uses existing data; good if evaluated honestly |
| 4 | Departure-time (dwell) forecast | Feasible now, visible on the dashboard, more data science than DE |
| 5 | ETA prediction | Strongest ML showcase, but depends on #1 and weeks of history |

Rejected: a dashboard chatbot / "ask your data" (you ruled it out; weak DE
story), an LLM daily summary (needs a paid key, text summarisation), and
"track adjudication" as a separate item (it overlaps #3, and rules already
remove impossible positions).

### 1. Destination entity resolution

- **Problem:** crews type destinations by hand ("BGO", "BERGEN HAVN", "BERGN",
  "NO BGO", "FOR ORDERS"). Mapping them to one UN/LOCODE is entity resolution.
- **Why AI fits:** exact matching fails on typos and abbreviations; a learned
  scorer can weigh spelling similarity together with context (distance from
  the ship, where it stopped next).
- **Approach:** clean the text → exact and alias matches → RapidFuzz top-5
  candidates → logistic regression (scikit-learn) turning features into a
  probability → below a threshold, answer "unknown". Logistic regression over
  XGBoost/LightGBM: few features, small data, well-calibrated probabilities.
- **Data needed:** start collecting destination, ETA, IMO and call sign in
  Bronze/Silver (not collected today; the live feed has no history, so every
  day of delay is lost). Labels come automatically from where each ship
  actually stopped next; optionally spot-check ~20 rows. Add major foreign
  ports to the port seed, or foreign destinations will be "unknown".
- **Complexity:** medium. Half a day for the collection change, then ~2–3 days
  once a week of data exists.
- **Cost:** free; seconds per run (only new texts), a tiny lookup table.
- **Open source:** yes (RapidFuzz, scikit-learn).
- **Integration:** new Silver columns; a Python step after the state machine
  writes `dim_destination_text` (text → locode, confidence, method,
  model_version); `fact_port_call` gains `declared_destination_locode` and
  `arrived_as_declared`; the confidence score gets a small boost when a ship
  stops where it said; the ship panel shows "Heading to". A pytest gate fails
  CI if precision or coverage drops.
- **Interviewer impact:** high. Textbook entity resolution, evaluated in CI,
  abstains instead of guessing, feeds the core metric.

### 2. Missing-quay discovery

- **Problem:** ~1,626 stops lie 10–25 km from any listed port (audit
  2026-09-28), likely real quays UN/LOCODE doesn't list, so they're labelled
  `at_sea` or matched to the wrong town.
- **Why AI fits:** unsupervised clustering finds dense groups of stop
  positions without a list of where quays are.
- **Approach:** HDBSCAN (scikit-learn) on stop positions from
  `fact_port_call`; each dense cluster far from a known port becomes a
  *candidate* quay with its centre, size and ship-type mix; a human approves
  candidates into a `local_quays` seed.
- **Data needed:** already in Gold.
- **Complexity:** low to medium (~1–2 days). **Cost:** free, seconds.
- **Integration:** a manual workflow (like the audit) writing a candidates
  report; approved rows extend `dim_port`, and `RULES_VERSION`/full refresh
  re-matches history.
- **Interviewer impact:** high for DE: improves reference data with evidence
  and keeps a human in the loop.

### 3. Unusual-behaviour detection (slim)

- **Problem:** flag stays much longer or shorter than normal for the port and
  ship type, stops in odd places, odd ship-days, repeated failed approaches.
- **Why AI fits:** partly. Use robust statistics (median and spread per port
  and ship type) for stay length, clustering for odd locations, Isolation
  Forest on ship-day features (speeds, stops, distance, signal gaps), and a
  plain rule for failed approaches.
- **Data needed:** existing `fact_port_call`, `ship_state_periods`,
  `fct_vessel_track`. No labels. Score within ship-type groups, or fishing
  boats dominate.
- **Evaluation:** a CI test injects fake anomalies (a 5-day stay, a stop in
  open sea, 60 knots) and checks they're caught.
- **Complexity:** medium (~2–3 days). **Cost:** free; seconds per run.
- **Integration:** new `fct_vessel_anomaly` table (type, score, reason,
  model_version) after `dbt build`; "Unusual this week" on the dashboard.
- **Interviewer impact:** medium to high, only with the honest evaluation.
  Unlabelled anomaly detection is easy to over-claim.

**As built (2026-10-04, `src/HarbourOS/anomalies.py`).** Maria picked this
first. Four kinds of flag, each with a plain-language reason, rebuilt every
run into the table `vessel_anomalies` and shown as "Unusual this week":

| Kind | How | Main guard against noise |
|---|---|---|
| Long / short stay | Log of the stay vs the median and median absolute deviation of that ship type at that port (all ports if under 10 stays); cut-off 3.5 | Long must be 24 h+; short only with a port-level baseline; a stay the ship itself makes 3+ times is its routine |
| Stop in open sea | Neighbour search (BallTree): no other ship stopped within 3 km this month | 20 km+ from a port; fishing boats, tugs and service boats left out |
| Unusual day | Isolation Forest per ship type on distance, top speed, share moving, longest silence and positions sent; top 0.3% of days | Whole days with 12+ positions only; reason names the rarest number |
| Impossible jump | Two positions 5-60 min apart implying over 60 knots and 2 km+ | One flag per ship, with how many days it happened |

Only ship MMSIs (200-799 million) are checked: the first real-data run
flagged search-and-rescue helicopters (MMSI 111...) "reaching 97 knots". Each
ship gets at most one flag per kind. Changes from the design: the
failed-approach rule was left out (needs reliable approach states first),
the table is `vessel_anomalies` (it is written by Python, not dbt), and odd
locations use a neighbour count, the core idea of DBSCAN, because the
question is "did anyone else stop here", not "where are the groups".

First real week (preview, 2026-10-04): 26 long stays, 39 short stays, 60
stops in open sea, 56 unusual days, 13 impossible jumps, in about 7 seconds.
Tuning on that data cut open-sea stops from 414 (coastal stops, fishing
boats, repeat visits) and short stays from 79 (ferries whose normal 50-minute
turnaround looked short next to overnight ferries). Examples: a ferry laid up
41 hours at a quay where ships stay 21 minutes; a fast rescue boat at 55
knots; a ship broadcasting as "FRENCH WARSHIP" jumping 1,029 km in 10 minutes.

*Evaluation:* `tests/test_anomalies.py` plants a 5-day stay, a lone stop in
open sea and a ship at 65 knots in a normal synthetic week and checks each is
caught, and that a fishing boat at sea, ships at a shared oil field, a ship's
own routine and an aircraft are not. There are no labels, so the real-data
flags are a list to look at, not a measured accuracy.

### 4. Departure-time (dwell) forecast

- **Problem:** for ships in port now, when will they likely leave?
- **Approach:** gradient boosting (scikit-learn `HistGradientBoostingRegressor`)
  or quantile regression on completed visits: port, ship type, arrival hour and
  weekday, ship's past stays. Must beat a baseline (median stay for port and
  type).
- **Data needed:** completed visits (`completeness = 'complete'`), available
  today; better after weeks.
- **Complexity:** medium (~2 days). **Cost:** free.
- **Integration:** predictions at build time for `in_port_now` ships; shown in
  the ship panel; an accuracy table that scores past predictions.
- **Interviewer impact:** medium; more ML than DE.

### 5. ETA prediction

- **Problem:** predict arrival time at the declared destination.
- **Approach:** `HistGradientBoostingRegressor` on distance remaining, speed,
  ship type, time of day and the crew's ETA; must beat the crew's ETA and
  distance ÷ speed.
- **Data needed:** #1 first, plus several weeks of voyages. Straight-line
  distance is poor in fjords.
- **Complexity:** high (~4–6 days after #1). **Cost:** free, small.
- **Limit:** updates only every ~5.5 hours.
- **Integration:** `fct_eta_prediction` and a self-updating `fct_eta_accuracy`.
- **Interviewer impact:** high if it beats baselines; later.

### Suggested order

Collect destination fields now → build #2 and #3 while the data accumulates →
#1 after about a week → #4 → #5 if still wanted.

---

## 10. Interview preparation

### The 30-second pitch

> "HarbourOS turns raw AIS ship positions from the Norwegian coast into port
> calls with a confidence score. It collects every ship every 10 minutes in
> GitHub Actions, cleans the data into a Silver layer where every rejected row
> is kept with a reason, runs a Python state machine that decides from GPS
> speed, not the crew's typed status, whether a ship is stopped, and builds a
> dbt star schema on MotherDuck. Everything is incremental, so it runs on free
> tiers, and a static map dashboard is rebuilt after each run. About 1.9
> million messages so far."

### Key technical decisions to talk about

1. Speed-first classification with status as evidence → confidence score.
2. Quarantine instead of delete; one CASE decides accept/reject.
3. Python only for sequential logic, SQL for everything else.
4. Incremental everywhere, including the proof that a tail rebuild equals a
   full rebuild.
5. Collection inside the run because the scheduler was unreliable.
6. Static dashboard built from Gold at deploy time; no exposed database.
7. Honest outputs: `at_sea`, completeness, `nearest_port_km`, "unknown".

### Concepts demonstrated

Medallion architecture, ELT, micro-batch ingestion, incremental processing
with watermarks, idempotent rebuilds, deduplication, data validation and
quarantine, data tests, schema evolution, backfills via rules versioning,
dimensional modelling (star schema, grain, business keys), orchestration
(GitHub Actions, Dagster), CI/CD, cost-aware cloud design, monitoring and
alerting.

### Hard problems solved

- Stitching sparse, jittery sightings into believable visits.
- Making cloud round trips affordable (~17,000 → ~40).
- Making the schedule reliable on top of an unreliable cron.
- Handling a schema change on an incremental model without manual steps.
- Removing false "complete" stays caused by collection gaps.

### Trade-offs you made

| Trade-off | Chosen | Given up |
|---|---|---|
| Freshness vs free tier | ~5.5-hour refresh | Hourly updates (storage cost) |
| Streaming vs batch | 10-minute polling, batch upload | Seconds-fresh data |
| Accuracy vs coverage in port matching | 10 km cutoff, `at_sea` label | Counting every stop as a port call |
| Precision vs recall in visits | ≥3 readings, gaps end visits | Some real short visits dropped |
| Dependency vs simplicity | Haversine in SQL, dbt as subprocess | Spatial extension, Dagster lineage per model |
| Static vs live dashboard | Static snapshot | Querying on demand |

### Likely questions, with answers grounded in the project

**Q: Why not trust the navigational status?**
A: It's typed by the crew and often stale. I saw a ship "moored" at 7 knots
for 11 hours. Speed is GPS-measured, so it decides the state; status only
raises or lowers confidence: 1.0 if they agree, 0.5 with no usable status,
0.3 if they contradict.

**Q: How do you know the port calls are right?**
A: The confidence score only says the ship's own signals agree, so I don't
use it as proof. Every run, 100 random port calls are checked against an
independent source, OpenStreetMap's map of quays and harbours: a call is
confirmed if the ship sat still within 300 m of one. The site shows the
latest result and lists the misses. It errs on the cautious side, because
OpenStreetMap leaves out some small quays.

**Q: How do you make it incremental?**
A: Silver uses a watermark derived from the data itself: every Bronze row ends
up in Silver or Quarantine, so the newest `received_at` in either is how far
we've got. For states, I keep a per-ship watermark and rebuild only from the
ship's last believable period before its new data. That gives exactly the same
result as a full rebuild, and a randomised test proves it. Gold is a dbt
incremental model with `delete+insert` keyed on ship.

**Q: Why key the incremental delete on `mmsi` and not the port call?**
A: A ship's visits are recomputed as a set. If new data merges two visits into
one, deleting by visit key would leave the old second visit behind.

**Q: Is it idempotent?**
A: The transforms are. Re-running Silver finds no new rows; states and port
calls are delete-then-insert per ship; the fact key is a deterministic hash.
Bronze is append-only, so a re-upload would add duplicate rows, but Silver
quarantines them as duplicates.

**Q: How do you handle data quality?**
A: Three levels: Silver rules with reasons, and an assertion that Silver plus
Quarantine equals Bronze; 32 dbt tests on every run, including a test that
every port coordinate lands on an exact arc-minute; and a read-only audit that
checks gaps, stay lengths, impossible time order and distance to ports.

**Q: What was the hardest bug?**
A: False 38-hour stays. When collection paused, a later sighting hours after
the gap counted as "we saw it leave", so 1,566 visits looked complete. I
changed the rule so a neighbouring state only counts within 30 minutes, and
added a rules version so a rule change automatically re-derives every ship.
A manual full refresh wasn't reliable because queued runs got replaced.

**Q: What happens when you add a column to the fact table?**
A: Incremental dbt models ignore new columns by default, which broke four
runs. Now the model fails on schema change, and the pipeline catches that
specific error and re-runs with `--full-refresh`.

**Q: How does it scale?**
A: Work per run tracks new data, not history. The limits are elsewhere: the
6-hour Actions job cap, MotherDuck's free storage (failsafe copies from table
rewrites), and the browser downloading ~7.5 MB. Next steps would be
partitioned or archived raw data and a retention policy.

**Q: Why is it not real-time?**
A: A stream needs an always-on consumer and more warehouse writes than the
free tier allows. Stops last tens of minutes to days, so 10-minute sampling
is enough for the question. I did try hourly publishing, and storage grew to
2.5 GB in a day and a half, so I went back.

**Q: How do you know a port match is right?**
A: I keep the distance, reject matches beyond 10 km as `at_sea`, and the audit
shows the distance distribution. The weak point is that UN/LOCODE marks towns,
not quays; I found cases like a ferry pier matched 2.65 km to the wrong port.
The fix I'd do next is clustering stop positions to find missing quays.

**Q: What would you do differently?**
A: Design for network latency and the scheduler from day one; put a retention
policy in early; collect the destination field from the start, since the live
feed has no history.

**Q: Did you use AI to build it?** *(be ready for this; the commits show a
Claude co-author.)*
A: Answer honestly: you used an AI coding assistant, and you made and can
defend the decisions. Pick two decisions from this file and explain them in
your own words.

---

## 11. LinkedIn content ideas

Write these in your own voice; the drafts are starting points. Keep numbers
exact, and don't claim anything in §8 "Not implemented".

### 1. Project announcement

- **Hook:** "A ship told the world it was moored. It was doing 7 knots."
- **Story:** why you built HarbourOS, what a port call is, and that the crew's
  status field can't be trusted, so speed decides and status only scores
  confidence.
- **Technical details:** BarentsWatch AIS, every ship every 10 minutes,
  Bronze/Silver/Gold on MotherDuck, dbt, GitHub Actions, static dashboard on
  Cloudflare Pages, all free tiers. ~1.9 million messages.
- **Visuals:** a screenshot of the map with a ship's route open; the
  architecture diagram from the README.
- **Tone sample:** "I wanted to know which ships called at which Norwegian
  ports, and for how long. The data doesn't say that. It says where a ship is,
  every few seconds, and what the crew typed into a box. Turns out the box is
  wrong a lot."

### 2. Technical deep dives

**a. "Incremental, but provably the same"**
- *Hook:* "My pipeline reprocesses 29% of the data each run and gets exactly
  the answer a full rebuild would. Here's how I checked."
- *Story:* the tail-restart idea, and the randomised test.
- *Details:* per-ship watermark, last believable period, 519k of 1.79M
  readings (run #94).
- *Visual:* a timeline sketch of one ship's states with the restart point.

**b. "Same code, 75 minutes instead of 4 seconds"**
- *Hook:* the number itself.
- *Story:* moving from a local DuckDB file to MotherDuck; row-by-row inserts
  and per-ship queries became thousands of round trips.
- *Details:* 500-row chunks, one query, Parquet staging: ~17,000 → ~40 trips.
- *Visual:* a before/after bar of round trips.

**c. "The coordinate format that moves ports 35 km"**
- *Hook:* "6753N is not 67.53 degrees."
- *Details:* degrees and minutes, the arc-minute test.
- *Visual:* a map with a port plotted both ways.

### 3. Data engineering lessons learned

- *Hook:* "Three things free tiers taught me about data engineering."
- *Points:* (1) GitHub's hourly cron started runs 3–6 hours apart, so I moved
  collection inside the run and made each run start the next; (2) MotherDuck
  storage grew to 2.5 GB in 1.5 days from failsafe copies, not data;
  (3) design for network round trips.
- *Visual:* the audit's gap-distribution table.

### 4. Challenges and solutions

**a. "The 38-hour stays that never happened"**
- *Hook:* "1,566 ships apparently stayed in port for exactly 38 hours."
- *Story:* the spike in the histogram, the collection gap, the rule fix, and
  the rules version that re-derives everything automatically.
- *Visual:* the stay-length chart before and after (if you have the old
  screenshot; otherwise describe it).

**b. "Adding one column broke my pipeline four times"**
- *Details:* dbt incremental models and `on_schema_change`.
- *Visual:* the failing Actions run list, then the green one.

**c. "Oil rigs are not ports"**
- *Details:* 14% of stops over 10 km from a port; `at_sea` label; Svalbard's
  separate country code.
- *Visual:* the "How close to a known port?" card.

### 5. AI integration journey (for later, once built)

- **Before building:** "I'm adding AI to my data pipeline. Here's why it's not
  a chatbot." Explain the destination-text problem and why a small scikit-learn
  model beats an LLM for it.
- **After collection starts:** show real messy destination strings you
  collected (only real ones).
- **After the model:** precision, coverage, the baseline it beat, and the CI
  gate. Share the misses too.
- **Missing quays:** a map of discovered clusters, and which ones turned out
  to be real quays.

**General style notes:** one idea per post; start with a concrete moment or
number; use "I" and plain words; say what went wrong; end with what you'd do
next or a genuine question, not a list of hashtags.

---

## 12. Appendix

### Timeline

| Date | Milestone |
|---|---|
| 2026-08-29 | Scaffold: pyproject, CI, pre-commit |
| 2026-08-30 | Day 2: Bronze ingestion (100 ships) |
| 2026-09-01 → 09-05 | Day 3: Silver with Quarantine; tests; NULL fix |
| 2026-09-05 | Day 4: 24-hour historic backfill (~28k rows) |
| 2026-09-06 | Day 4–5: state machine; port calls (163 events) |
| 2026-09-07 | Day 6: dbt Gold with UN/LOCODE (28 checks) |
| 2026-09-08 | Day 7: Dagster |
| 2026-09-10 | Day 8: dashboard on Observable Framework + Cloudflare Pages; stop dropping Bronze |
| 2026-09-13 → 09-14 | Incremental Silver, per-vessel rebuilds, incremental fact; all ships |
| 2026-09-16 | MotherDuck; GitHub Actions pipeline |
| 2026-09-18 | ~17,000 round trips → ~40 |
| 2026-09-22 | "Hourly" pipeline |
| 2026-09-25 | CLAUDE.md for AI-assisted development |
| 2026-09-26 | PR #1–#4: auto deploy, Node 24, 10-minute collection |
| 2026-09-27 | PR #5–#8: audit, Svalbard, `at_sea`, "in port now" |
| 2026-09-28 | PR #9–#15: auto full refresh, chained runs + alerts, freshness, README, map redesign, completeness fix |
| 2026-09-29/30 | PR #16–#19: header, safety checks, back to ~4 runs/day, search and Norway time |
| 2026-10-01 | PR #21–#22, #25–#26: location evidence in the confidence score, destination and ETA collected; automatic reliability check against OpenStreetMap; fish-farm stops labelled |
| 2026-10-02 | PR #28–#30: npm cache and retry; score hidden; port calls need an official Kystverket berth (run #115: 77/100). Found that destinations and status never arrived ("Simple" model) and that Kystdatahuset moved address |

### Key numbers (source and date)

| Number | Value | Source |
|---|---|---|
| Bronze / Silver / Quarantine rows | 1,884,987 / 1,791,808 / 93,179 | Run #94 log, 2026-09-30 02:02 UTC |
| Quarantine reasons | duplicate 86,611; missing_speed 5,680; invalid_mmsi_range 731; invalid_speed 157 | Run #94 |
| State periods | 157,036 | Run #94 |
| Port-call events | 20,365 (4,127 fully observed) | Run #94 |
| Messages per run | 113,147 from 33 polls | Run #94 |
| Ships per poll | ~4,145 | Run #94 |
| dbt | 40 nodes passed, 32 tests | Run #94 |
| Route file | 290,267 points, 3.1 MB | Run #94 |
| Site size | page 57 kB, imports 1.95 MB, files 7.5 MB | Run #94 |
| Port seed | 610 seaports (NO + SJ) | `dbt/seeds` |
| Tests | 64 passed, 83% coverage | Local run, 2026-09-30 |
| Audit (pre-PR #15) | 12,267 port calls, 78% complete, avg confidence 0.56, 16% of stops >10 km from a port | Audit run, 2026-09-28 |
| Warehouse size | 157 MB | 2026-09-29 |

### Scripts

| Script | Purpose |
|---|---|
| `fetch_ports.py` | Download UN/LOCODE and write the NO + SJ seaport seed |
| `backfill_historic.py` | One-off 24-hour historic backfill (local DB) |
| `migrate_to_motherduck.py` | One-off copy of the Python-owned tables to MotherDuck |
| `preview_states.py` | Print a ship's derived states to eyeball them |
| `explore_data.py`, `inspect_bronze.py` | Print tables and schemas from the local DB |
| `check_api_response.py`, `test_auth.py` | Check the API and credentials by hand |

### Things still marked "Not recorded"

- Why AIS and port calls were chosen in the first place (§1).
- Why DuckDB was picked over other databases at the start, and the exact
  reasoning for the MotherDuck move.
- Why streaming wasn't attempted.
- The meaning of the `stream` column.
- Current distinct ship count, total raw bytes received, BarentsWatch rate
  limits, page load times, MotherDuck compute per run.

Fill these in when you can; they're likely interview questions.
