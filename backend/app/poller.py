from __future__ import annotations

import hashlib
import logging
import threading
from datetime import datetime, timezone

from app.crypto import decrypt_secret
from app.db import SessionLocal, Service, Settings, get_settings, insert_log_ignore, trim_service_logs
from app.dokploy import DokployClient, DokployError
from app.filters import classify_level, parse_logs, should_keep

logger = logging.getLogger(__name__)
poll_lock = threading.Lock()


def _hash_line(service_id: int, timestamp: datetime | None, message: str) -> str:
    ts = timestamp.isoformat() if timestamp else ""
    payload = f"{service_id}|{ts}|{message}".encode("utf-8", errors="replace")
    return hashlib.sha256(payload).hexdigest()


def _client(settings: Settings) -> DokployClient | None:
    url = (settings.dokploy_url or "").strip()
    if not url or not settings.dokploy_api_key_enc:
        return None
    try:
        key = decrypt_secret(settings.dokploy_api_key_enc)
    except RuntimeError:
        logger.exception("Cannot decrypt Dokploy API key")
        return None
    if not key:
        return None
    return DokployClient(url, key)


def _compose_containers(client: DokployClient, service: Service) -> list[tuple[str, str]]:
    app_name = service.app_name
    compose_type = service.compose_type or "docker-compose"
    if not app_name:
        details = client.compose_one(service.compose_id)
        app_name = details.get("appName") or ""
        compose_type = details.get("composeType") or compose_type
        service.app_name = app_name
        service.compose_type = compose_type
    if not app_name:
        return []
    app_type = "stack" if compose_type == "stack" else "docker-compose"
    containers = client.containers_by_app_name(app_name, app_type)
    result: list[tuple[str, str]] = []
    for container in containers:
        cid = container.get("containerId") or container.get("id") or ""
        name = container.get("name") or cid
        state = (container.get("state") or "").lower()
        if cid and state in ("", "running", "restarting"):
            result.append((cid, name))
        elif cid and not state:
            result.append((cid, name))
    if not result:
        for container in containers:
            cid = container.get("containerId") or ""
            if cid:
                result.append((cid, container.get("name") or cid))
    return result


def _ingest(
    session,
    settings: Settings,
    service: Service,
    raw: str,
    source_label: str,
) -> int:
    added = 0
    for original, timestamp, message in parse_logs(raw):
        level = classify_level(message)
        if not should_keep(
            message,
            level,
            level_filter=settings.level_filter,
            exclude_patterns=settings.exclude_patterns or [],
            exclude_regex=settings.exclude_regex or [],
            keywords=settings.keywords or [],
            keyword_mode=settings.keyword_mode or "any",
        ):
            continue
        line_hash = _hash_line(service.id, timestamp, message)
        if insert_log_ignore(
            session,
            service_id=service.id,
            timestamp=timestamp,
            level=level,
            message=message,
            raw=original,
            source_label=source_label,
            line_hash=line_hash,
        ):
            added += 1
    return added


def poll_service(session, settings: Settings, service: Service, client: DokployClient) -> None:
    if (
        settings.self_application_id
        and service.application_id
        and service.application_id == settings.self_application_id
    ):
        service.last_error = "skipped (self_application_id)"
        return

    added = 0
    if service.dokploy_type == "application":
        raw = client.application_read_logs(
            service.application_id,
            settings.log_tail,
            settings.log_since,
        )
        added += _ingest(session, settings, service, raw, service.name)
    elif service.dokploy_type == "compose":
        containers = _compose_containers(client, service)
        if not containers:
            raise DokployError("No running containers for compose service")
        service.container_id = containers[0][0]
        for container_id, container_name in containers:
            raw = client.compose_read_logs(
                service.compose_id,
                container_id,
                settings.log_tail,
                settings.log_since,
            )
            label = f"{service.name} / {container_name}"
            added += _ingest(session, settings, service, raw, label)
    else:
        raise DokployError(f"Unknown service type {service.dokploy_type}")

    trim_service_logs(session, service.id, settings.max_lines_per_service)
    service.last_fetch_at = datetime.now(timezone.utc).replace(tzinfo=None)
    service.last_error = None
    logger.info("Fetched logs for %s (%s new)", service.name, added)


def run_poll_cycle() -> dict:
    if not poll_lock.acquire(blocking=False):
        return {"status": "busy"}
    try:
        with SessionLocal() as session:
            settings = get_settings(session)
            client = _client(settings)
            if client is None:
                return {"status": "skipped", "reason": "dokploy is not configured"}
            services = (
                session.query(Service)
                .filter(Service.enabled.is_(True))
                .order_by(Service.name)
                .all()
            )
            ok = 0
            failed = 0
            for service in services:
                try:
                    poll_service(session, settings, service, client)
                    ok += 1
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Poll failed for %s", service.name)
                    service.last_error = str(exc)[:2000]
                    failed += 1
                session.commit()
            return {"status": "ok", "polled": ok, "failed": failed}
    finally:
        poll_lock.release()
