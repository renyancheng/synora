# Deploy Layout

- `deploy/dev`: local development stack
- `deploy/prod`: production/server stack

Both directories are self-contained. Copy `.env.example` to `.env`, then run:

```bash
docker compose up --build
```

Recommended usage:

- Local development: `cd deploy/dev`
- Production server: `cd deploy/prod`
