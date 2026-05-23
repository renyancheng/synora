# Synora MVP

Synora is a mobile-first life memo assistant for a single teacher user. This repository now contains:

- `app/synora`: Flutter Android-first client
- `services/api`: FastAPI backend with approval-gated schedule and quick note flows
- `deploy`: Docker Compose for local private deployment

## MVP scope

- Email login for the bootstrapped single user
- Text-first schedule parsing and conflict detection
- Approval-gated schedule creation
- Approval-gated quick notes with topic tag suggestions
- Email reminders plus mock WeCom delivery audit

## Quick start

1. Copy `deploy/.env.example` if you need custom credentials or SMTP targets.
2. Start the backend stack with:

```powershell
cd deploy
docker compose up --build
```

3. Run the Flutter app and point it to the API:

```powershell
cd app\synora
flutter run --dart-define=SYNORA_API_BASE_URL=http://10.0.2.2:8000
```

The default login matches `deploy/.env.example`.
