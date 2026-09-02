# Log Accumulator

Собирает логи warning/error (и при необходимости остальные) из applications и Compose-стеков одного проекта Dokploy. Опрашивает REST Dokploy примерно раз в минуту. Все рабочие настройки — в UI; в переменных окружения только секреты и DSN MariaDB.

Один контейнер отдаёт и API, и UI (FastAPI + собранный SPA).

## Стек

- FastAPI + SQLAlchemy, внешняя MariaDB
- Vite + React SPA, тот же процесс uvicorn
- Вход: пароль `APP_PASSWORD`, сессия в cookie

## Переменные окружения

| Переменная | Обязательно | Назначение |
|---|---|---|
| `APP_PASSWORD` | да | Пароль входа в UI |
| `SESSION_SECRET` | да | Подпись cookie сессии (длинная случайная строка) |
| `ENCRYPTION_KEY` | да | Шифрование API-ключа Dokploy в БД (другая случайная строка) |
| `DATABASE_URL` | да | `mysql+pymysql://user:pass@host:3306/dbname?charset=utf8mb4` |
| `PORT` | нет | По умолчанию `8000` |
| `SESSION_SECURE` | нет | `true`, если приложение открывается только по HTTPS |

URL Dokploy, API-ключ, проект, список сервисов, интервал опроса и фильтры задаются после входа на странице **Settings**.

Пример значений — [`.env.example`](.env.example).

## Развёртывание в Dokploy

Контейнер приложения **не** поднимает БД. Нужны Application из этого репозитория и сервис **MariaDB** в том же проекте.

### 1. MariaDB

1. В проекте Dokploy: **Add service → Database → MariaDB** (или возьмите уже существующую).
2. Запомните внутренний hostname (часто это `appName` сервиса, например `project-mariadb-xxxxxx`), имя БД, пользователя и пароль.
3. Дождитесь статуса **done** / running.

### 2. Application

1. **Add service → Application**.
2. Provider: Git (этот репозиторий) или Dockerfile из исходников.
3. Build: **Dockerfile**, путь `Dockerfile` в корне репозитория.
4. Порт контейнера: **8000** (или значение `PORT`, если зададите другое).
5. Добавьте домен (Domains), чтобы открыть UI снаружи.

### 3. Сеть до MariaDB

Контейнер приложения должен резолвить hostname MariaDB:

- оба сервиса в одном проекте Dokploy; при необходимости подключите Application к той же Docker-сети, что и MariaDB (`dokploy-network` или сеть БД);
- в `DATABASE_URL` указывайте **внутренний** hostname и порт `3306`, не публичный IP и не внешний домен БД.

Пример:

```text
mysql+pymysql://USER:PASSWORD@project-mariadb-xxxxxx:3306/log_accumulator?charset=utf8mb4
```

Volume у Application не нужен: данные живут в MariaDB.

### 4. Environment

В Environment приложения задайте:

```text
APP_PASSWORD=...
SESSION_SECRET=...
ENCRYPTION_KEY=...
DATABASE_URL=mysql+pymysql://USER:PASSWORD@HOST:3306/DBNAME?charset=utf8mb4
PORT=8000
SESSION_SECURE=true
```

`SESSION_SECURE=true` имеет смысл, если UI открывается по HTTPS.

Сгенерировать секреты:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Дважды для `SESSION_SECRET` и `ENCRYPTION_KEY`.

### 5. Деплой и первая настройка

1. **Deploy** и дождитесь успешного билда (сборка фронта + Python-образ).
2. Откройте домен приложения, войдите с `APP_PASSWORD`.
3. **Settings**:
   - **URL** — origin дашборда Dokploy, например `https://dokploy.example.com` (без `/dashboard`).
   - **API key** — Profile → API/CLI. Нужен ключ owner/admin: для Compose нужен доступ к Docker (`docker.read`).
   - **Load projects** → выберите проект → **Sync list from project**.
   - Включите нужные сервисы, **Save enabled services**.
   - В **Skip this application ID** укажите `applicationId` самого аккумулятора, чтобы не опрашивать себя.
4. **Save settings**, затем **Poll now** (или подождите фоновый интервал, по умолчанию 60 с).

Клики по строке лога и по сервису открывают соответствующую страницу в Dokploy (`?tab=logs`).

### Если контейнер не стартует

- MariaDB ещё не готова: приложение ретраит коннект при старте; если БД так и не поднялась — смотрите логи Application.
- `DATABASE_URL`: неверный host/user/password или сервис не в той сети.
- Нет `APP_PASSWORD` / `SESSION_SECRET` / `ENCRYPTION_KEY` — процесс сразу завершится.
- UI 404 при живом API: билд фронта не попал в образ (должен быть `npm run build` в Dockerfile).

### `application.readLogs` → 404 Not found

Эндпоинт `*.readLogs` есть не во всех версиях Dokploy. Если `application.one` с тем же id даёт 200, клиент сам уходит на запасной путь:

1. `application.one` / `compose.one` → `appName`, `serverId`
2. `docker.getContainersByAppNameMatch` → `containerId`
3. WebSocket ` /docker-container-logs` с заголовком `x-api-key` (как UI Dokploy, но без cookie)

Нужен ключ owner/admin с доступом к Docker. После обновления кода сделайте redeploy аккумулятора.

## Локальная разработка

MariaDB уже должна быть запущена (локально или в Docker).

```bash
export APP_PASSWORD=dev
export SESSION_SECRET=dev-session-secret
export ENCRYPTION_KEY=dev-encryption-key
export DATABASE_URL=mysql+pymysql://user:pass@127.0.0.1:3306/log_accumulator?charset=utf8mb4

cd backend && pip install -r requirements.txt
PYTHONPATH=. uvicorn app.main:app --reload --port 8000

# другой терминал
cd frontend && npm install && npm run dev
```

UI: `http://127.0.0.1:5173` (проксирует `/api` на порт 8000).

Собранный образ как в проде:

```bash
docker build -t log-accumulator .
docker run --rm -p 8000:8000 \
  -e APP_PASSWORD=dev \
  -e SESSION_SECRET=dev-session-secret \
  -e ENCRYPTION_KEY=dev-encryption-key \
  -e DATABASE_URL=mysql+pymysql://user:pass@host.docker.internal:3306/log_accumulator?charset=utf8mb4 \
  log-accumulator
```

## Фильтры

Применяются при записи лога, по порядку:

1. Уровень: `off` | `warning_error` | `error_only`
2. Исключения: подстроки и regex (совпавшие строки отбрасываются)
3. Ключевые слова `any` / `all` (пустой список — оставить всё, что прошло шаги 1–2)
