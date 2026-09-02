from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from starlette.middleware.sessions import SessionMiddleware

from app.config import APP_PASSWORD, SESSION_SECRET, SESSION_SECURE
from app.crypto import decrypt_secret, encrypt_secret
from app.db import LogLine, Service, SessionLocal, get_settings, init_db, wait_for_db
from app.dokploy import DokployClient, DokployError, flatten_projects, list_project_services
from app.poller import run_poll_cycle

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await asyncio.to_thread(wait_for_db)
    await asyncio.to_thread(init_db)
    stop = asyncio.Event()

    async def loop():
        while not stop.is_set():
            try:
                await asyncio.to_thread(run_poll_cycle)
            except Exception:
                logger.exception("Background poll failed")
            interval = 60
            try:
                with SessionLocal() as session:
                    interval = max(15, get_settings(session).poll_interval_sec or 60)
            except Exception:
                logger.exception("Could not read poll interval")
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                pass

    task = asyncio.create_task(loop())
    yield
    stop.set()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Log Accumulator", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    session_cookie="la_session",
    same_site="lax",
    https_only=SESSION_SECURE,
    max_age=60 * 60 * 24 * 14,
)


def require_auth(request: Request) -> None:
    if not request.session.get("auth"):
        raise HTTPException(status_code=401, detail="Unauthorized")


class LoginBody(BaseModel):
    password: str


class SettingsUpdate(BaseModel):
    dokploy_url: str | None = None
    dokploy_api_key: str | None = None
    project_id: str | None = None
    poll_interval_sec: int | None = Field(default=None, ge=15, le=3600)
    log_since: str | None = None
    log_tail: int | None = Field(default=None, ge=1, le=10000)
    level_filter: str | None = None
    exclude_patterns: list[str] | None = None
    exclude_regex: list[str] | None = None
    keywords: list[str] | None = None
    keyword_mode: str | None = None
    max_lines_per_service: int | None = Field(default=None, ge=50, le=20000)
    self_application_id: str | None = None


class ServicePatch(BaseModel):
    id: int
    enabled: bool


class ServicesPatchBody(BaseModel):
    services: list[ServicePatch]


def _settings_public(settings) -> dict[str, Any]:
    return {
        "dokploy_url": settings.dokploy_url or "",
        "has_api_key": bool(settings.dokploy_api_key_enc),
        "project_id": settings.project_id or "",
        "poll_interval_sec": settings.poll_interval_sec,
        "log_since": settings.log_since,
        "log_tail": settings.log_tail,
        "level_filter": settings.level_filter,
        "exclude_patterns": settings.exclude_patterns or [],
        "exclude_regex": settings.exclude_regex or [],
        "keywords": settings.keywords or [],
        "keyword_mode": settings.keyword_mode,
        "max_lines_per_service": settings.max_lines_per_service,
        "self_application_id": settings.self_application_id or "",
    }


def _service_dict(service: Service, log_count: int = 0) -> dict[str, Any]:
    return {
        "id": service.id,
        "external_key": service.external_key,
        "dokploy_type": service.dokploy_type,
        "application_id": service.application_id,
        "compose_id": service.compose_id,
        "container_id": service.container_id,
        "name": service.name,
        "project_id": service.project_id,
        "environment_id": service.environment_id,
        "dokploy_path": service.dokploy_path,
        "enabled": service.enabled,
        "last_fetch_at": service.last_fetch_at.isoformat() if service.last_fetch_at else None,
        "last_error": service.last_error,
        "log_count": log_count,
    }


@app.post("/api/login")
def login(body: LoginBody, request: Request):
    if not hmac.compare_digest(
        hashlib.sha256(body.password.encode("utf-8")).digest(),
        hashlib.sha256(APP_PASSWORD.encode("utf-8")).digest(),
    ):
        raise HTTPException(status_code=401, detail="Invalid password")
    request.session["auth"] = True
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/me")
def me(_: None = Depends(require_auth)):
    return {"ok": True}


@app.get("/api/settings")
def read_settings(_: None = Depends(require_auth)):
    with SessionLocal() as session:
        return _settings_public(get_settings(session))


@app.put("/api/settings")
def update_settings(body: SettingsUpdate, _: None = Depends(require_auth)):
    with SessionLocal() as session:
        settings = get_settings(session)
        data = body.model_dump(exclude_unset=True)
        if "dokploy_api_key" in data:
            key = (data.pop("dokploy_api_key") or "").strip()
            if key:
                settings.dokploy_api_key_enc = encrypt_secret(key)
        for field, value in data.items():
            if value is None:
                continue
            if field in {"dokploy_url"}:
                value = str(value).rstrip("/")
            if field == "level_filter" and value not in {
                "off",
                "warning_error",
                "error_only",
            }:
                raise HTTPException(status_code=400, detail="Invalid level_filter")
            if field == "keyword_mode" and value not in {"any", "all"}:
                raise HTTPException(status_code=400, detail="Invalid keyword_mode")
            if field == "log_since" and not (
                value == "all" or (len(value) >= 2 and value[-1] in "smhd" and value[:-1].isdigit())
            ):
                raise HTTPException(status_code=400, detail="Invalid log_since")
            setattr(settings, field, value)
        session.commit()
        session.refresh(settings)
        return _settings_public(settings)


def _dokploy_from_settings(settings) -> DokployClient:
    if not settings.dokploy_url or not settings.dokploy_api_key_enc:
        raise HTTPException(status_code=400, detail="Configure Dokploy URL and API key first")
    try:
        api_key = decrypt_secret(settings.dokploy_api_key_enc)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return DokployClient(settings.dokploy_url, api_key)


@app.get("/api/dokploy/projects")
def dokploy_projects(_: None = Depends(require_auth)):
    with SessionLocal() as session:
        client = _dokploy_from_settings(get_settings(session))
    try:
        projects = client.project_all()
    except DokployError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"projects": flatten_projects(projects)}


@app.post("/api/services/sync")
def sync_services(_: None = Depends(require_auth)):
    with SessionLocal() as session:
        settings = get_settings(session)
        if not settings.project_id:
            raise HTTPException(status_code=400, detail="Select a project first")
        client = _dokploy_from_settings(settings)
        try:
            catalog = list_project_services(client.project_all(), settings.project_id)
        except DokployError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        seen = set()
        for item in catalog:
            seen.add(item["external_key"])
            row = session.query(Service).filter(Service.external_key == item["external_key"]).one_or_none()
            if row is None:
                row = Service(external_key=item["external_key"], enabled=False)
                session.add(row)
            row.dokploy_type = item["dokploy_type"]
            row.application_id = item["application_id"]
            row.compose_id = item["compose_id"]
            row.name = item["name"]
            row.project_id = item["project_id"]
            row.environment_id = item["environment_id"]
            row.dokploy_path = item["dokploy_path"]
        session.commit()
        return {"ok": True, "count": len(seen)}


@app.get("/api/services")
def list_services(_: None = Depends(require_auth)):
    with SessionLocal() as session:
        counts = dict(
            session.execute(
                select(LogLine.service_id, func.count(LogLine.id)).group_by(LogLine.service_id)
            ).all()
        )
        rows = session.query(Service).order_by(Service.name).all()
        return {"services": [_service_dict(s, counts.get(s.id, 0)) for s in rows]}


@app.patch("/api/services")
def patch_services(body: ServicesPatchBody, _: None = Depends(require_auth)):
    with SessionLocal() as session:
        ids = {item.id: item.enabled for item in body.services}
        rows = session.query(Service).filter(Service.id.in_(ids.keys())).all()
        for row in rows:
            row.enabled = ids[row.id]
        session.commit()
        return {"ok": True}


@app.get("/api/logs")
def list_logs(
    service_id: int | None = None,
    level: str | None = None,
    q: str | None = None,
    limit: int = 400,
    _: None = Depends(require_auth),
):
    limit = max(1, min(limit, 2000))
    with SessionLocal() as session:
        query = session.query(LogLine, Service).join(Service, LogLine.service_id == Service.id)
        if service_id:
            query = query.filter(LogLine.service_id == service_id)
        if level:
            query = query.filter(LogLine.level == level)
        if q:
            query = query.filter(LogLine.message.ilike(f"%{q}%"))
        rows = (
            query.order_by(LogLine.timestamp.desc(), LogLine.id.desc()).limit(limit).all()
        )
        items = []
        for line, service in rows:
            items.append(
                {
                    "id": line.id,
                    "service_id": service.id,
                    "service_name": service.name,
                    "source_label": line.source_label or service.name,
                    "dokploy_path": service.dokploy_path,
                    "timestamp": line.timestamp.isoformat() if line.timestamp else None,
                    "level": line.level,
                    "message": line.message,
                }
            )
        return {"logs": items}


@app.post("/api/poll/now")
def poll_now(_: None = Depends(require_auth)):
    result = run_poll_cycle()
    if result.get("status") == "busy":
        raise HTTPException(status_code=409, detail="Poll already running")
    return result


assets_dir = STATIC_DIR / "assets"
if assets_dir.exists():
    app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")


@app.get("/{full_path:path}")
def spa(full_path: str):
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not found")
    index = STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    raise HTTPException(status_code=404, detail="UI is not built")
