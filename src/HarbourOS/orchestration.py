"""Dagster orchestration: the pipeline as a dependency graph, on a schedule.

Until now each layer was run by hand, in the right order, by remembering the
right order. Dagster makes that order explicit and enforced: every table is an
asset that declares its upstreams, so the system works out what to run and
when, keeps run history, and retries what fails.

Note on dbt: the dagster-dbt integration (which would expose each dbt model as
its own asset) pins dbt-core below 1.12, and that older dbt pulls a mashumaro
release predating Python 3.14 -- it crashes on import here. Rather than pin the
whole project backwards for a convenience, dbt runs as a plain subprocess step.
Ordering, logging and failure handling are unaffected; only the per-model
lineage detail is lost. Worth revisiting when dagster-dbt supports dbt 1.12.
"""

import subprocess
from pathlib import Path

from dagster import (
    AssetExecutionContext,
    AssetKey,
    Definitions,
    ScheduleDefinition,
    asset,
    define_asset_job,
)
from HarbourOS.ingestion import ingest_batch
from HarbourOS.transform import (
    run_port_calls_transform,
    run_silver_transform,
    run_state_periods_transform,
)

DBT_PROJECT_DIR = Path(__file__).resolve().parents[2] / "dbt"

BRONZE_KEY = AssetKey(["harbouros", "ais_messages_bronze"])
SILVER_KEY = AssetKey(["harbouros", "ais_messages_silver"])
PERIODS_KEY = AssetKey(["harbouros", "ship_state_periods"])
CALLS_KEY = AssetKey(["harbouros", "port_call_events"])
GOLD_KEY = AssetKey(["harbouros", "gold_star_schema"])


@asset(key=BRONZE_KEY, compute_kind="python", group_name="ingestion")
def ais_messages_bronze(context: AssetExecutionContext) -> None:
    """Raw AIS positions polled from the BarentsWatch live API."""
    ingest_batch(limit=100)


@asset(key=SILVER_KEY, deps=[BRONZE_KEY], compute_kind="sql", group_name="cleaning")
def ais_messages_silver(context: AssetExecutionContext) -> None:
    """Deduplicated, validated positions -- plus the quarantine audit trail."""
    run_silver_transform()


@asset(
    key=PERIODS_KEY,
    deps=[SILVER_KEY],
    compute_kind="python",
    group_name="state_machine",
)
def ship_state_periods(context: AssetExecutionContext) -> None:
    """Confidence-scored states derived by the port-call state machine."""
    run_state_periods_transform()


@asset(
    key=CALLS_KEY,
    deps=[PERIODS_KEY],
    compute_kind="python",
    group_name="state_machine",
)
def port_call_events(context: AssetExecutionContext) -> None:
    """One row per visit, grouped from consecutive state periods."""
    run_port_calls_transform()


@asset(key=GOLD_KEY, deps=[CALLS_KEY], compute_kind="dbt", group_name="warehouse")
def gold_star_schema(context: AssetExecutionContext) -> None:
    """The Gold layer: dbt builds every model and runs every data test."""
    result = subprocess.run(
        ["dbt", "build", "--profiles-dir", "."],
        cwd=DBT_PROJECT_DIR,
        capture_output=True,
        text=True,
        check=False,
    )

    context.log.info(result.stdout)
    if result.returncode != 0:
        context.log.error(result.stderr)
        raise RuntimeError("dbt build failed -- see the logs above")


harbouros_job = define_asset_job(name="harbouros_pipeline", selection="*")

harbouros_schedule = ScheduleDefinition(
    name="every_15_minutes",
    job=harbouros_job,
    cron_schedule="*/15 * * * *",
)

defs = Definitions(
    assets=[
        ais_messages_bronze,
        ais_messages_silver,
        ship_state_periods,
        port_call_events,
        gold_star_schema,
    ],
    schedules=[harbouros_schedule],
)
