# Synora API

FastAPI backend for the Synora MVP.

## Highlights

- Single-user email login bootstrapped from environment
- Text-first schedule draft parsing and conflict detection
- Approval-gated writes for schedules and quick notes
- PostgreSQL persistence with Redis-backed Celery workers
- SMTP email reminder delivery with notification audit trail

## Local structure

- `app/`: application code
- `tests/`: unit tests for parser and approval flows
- `requirements.txt`: runtime dependencies

## Run locally

The recommended path is Docker Compose from `deploy/docker-compose.yml`.

For manual development:

1. Create a Python 3.12 virtual environment.
2. Install `requirements.txt`.
3. Export the environment variables from `deploy/.env.example`.
4. Start the API with `uvicorn app.main:app --reload --app-dir services/api`.

## Default credentials

The first bootstrapped user is controlled by:

- `SYNORA_BOOTSTRAP_EMAIL`
- `SYNORA_BOOTSTRAP_PASSWORD`
- `SYNORA_BOOTSTRAP_DISPLAY_NAME`
