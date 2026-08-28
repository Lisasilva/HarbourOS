# HarbourOS: Norwegian Maritime AIS Port-Call Intelligence

Real-time streaming data platform that converts raw AIS ship tracking data into structured port-call events.

## Quick Start

```bash
# Install dependencies
uv sync --all-extras

# Run pre-commit setup
pre-commit install

# Run tests
pytest tests/ -v
```

## Architecture

- **Bronze**: Raw AIS messages from BarentsWatch Live API (Server-Sent Events)
- **Silver**: Deduplicated, typed messages with plausibility checks
- **Gold**: Star schema with port-call fact table and vessel dimensions

## Development

```bash
# Local development environment
docker compose up

# Run full test suite
pytest tests/ --cov=src/HarbourOS
```

## Team

Portfolio project for data engineering interviews.
