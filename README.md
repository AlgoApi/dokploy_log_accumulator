# Log Accumulator

Collects warning/error (and optionally other) logs from Dokploy applications and Compose stacks in one project. Polls Dokploy REST about once a minute. Settings live in the UI; only secrets and the MariaDB DSN stay in environment variables.

## Stack

- FastAPI + SQLAlchemy, MariaDB (external)
- Vite + React SPA, served by the same process
- Login: `APP_PASSWORD` in env, session cookie

## Environment

| Variable | Required | Purpose |
|---|---|---|
| `APP_PASSWORD` | yes | UI login password |
| `SESSION_SECRET` | yes | Signs the session cookie |
| `ENCRYPTION_KEY` | yes | Encrypts the Dokploy API key at rest |
| `DATABASE_URL` | yes | `mysql+pymysql://user:pass@host:3306/dbname?charset=utf8mb4` |
| `PORT` | no | Default `8000` |
| `SESSION_SECURE` | no | Set `true` if the app is only served over HTTPS |

Dokploy URL, API key, project, tracked services, poll interval, and filters are configured after login on **Settings**.

## Deploy on Dokploy

1. Create (or reuse) a **MariaDB** service in the same project. Note the internal hostname, database, user, and password.
2. Create an **Application** from this repository (Dockerfile).
3. Attach the application to the same Docker network as MariaDB so `DATABASE_URL` host resolves (Dokploy network / shared network).
4. Set the env vars above. Use the **internal** MariaDB hostname, not a public IP.
5. Deploy. Open the application URL, sign in, fill Settings:
   - Dokploy URL (the dashboard origin, e.g. `https://dokploy.example.com`)
   - API key from Dokploy Profile → API/CLI (owner or admin; Compose container lookup needs Docker read)
   - Load projects, pick the project, sync services, enable the ones to watch
   - Put this application’s Dokploy `applicationId` in “Skip this application ID”
6. Click **Poll now** or wait for the background interval.

MariaDB holds all persistent data. The application container does not need a volume.

## Local development

```bash
# MariaDB must already be running
export APP_PASSWORD=dev
export SESSION_SECRET=dev-session-secret
export ENCRYPTION_KEY=dev-encryption-key
export DATABASE_URL=mysql+pymysql://user:pass@127.0.0.1:3306/log_accumulator

cd backend && pip install -r requirements.txt
PYTHONPATH=. uvicorn app.main:app --reload --port 8000

# other terminal
cd frontend && npm install && npm run dev
```

UI: `http://127.0.0.1:5173` (proxies `/api` to port 8000).

## Filters

Applied on ingest, in order:

1. Level: `off` | `warning_error` | `error_only`
2. Exclude substrings / regex (drop matching lines)
3. Keywords `any` / `all` (empty list keeps everything that passed 1–2)
