from __future__ import annotations

import hashlib
import logging
import math
from datetime import datetime, timezone

from app.db import LogLine, SessionLocal, Settings, Service, get_settings, insert_log_ignore, trim_service_logs
from app.filters import classify_level, parse_logs, should_keep

logger = logging.getLogger(__name__)


def line_hash(service_id: int, timestamp: datetime | None, message: str) -> str:
    ts = timestamp.isoformat() if timestamp else ""
    payload = f"{service_id}|{ts}|{message}".encode("utf-8", errors="replace")
    return hashlib.sha256(payload).hexdigest()


def ingest_parsed_line(
    session,
    settings: Settings,
    service: Service,
    original: str,
    timestamp: datetime | None,
    message: str,
    source_label: str,
) -> bool:
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
        return False
    h = line_hash(service.id, timestamp, message)
    return insert_log_ignore(
        session,
        service_id=service.id,
        timestamp=timestamp,
        level=level,
        message=message,
        raw=original,
        source_label=source_label,
        line_hash=h,
    )


def ingest_log_text(
    session,
    settings: Settings,
    service: Service,
    raw: str,
    source_label: str,
) -> int:
    added = 0
    for original, timestamp, message in parse_logs(raw):
        if ingest_parsed_line(session, settings, service, original, timestamp, message, source_label):
            added += 1
    return added


def ingest_log_line(
    session,
    settings: Settings,
    service: Service,
    line: str,
    source_label: str,
) -> int:
    trimmed = line.strip()
    if not trimmed:
        return 0
    rows = parse_logs(trimmed)
    if not rows:
        return 0
    added = 0
    for original, timestamp, message in rows:
        if ingest_parsed_line(session, settings, service, original, timestamp, message, source_label):
            added += 1
    return added


def last_seen_at(service_id: int, source_label: str) -> datetime | None:
    with SessionLocal() as session:
        q = session.query(LogLine.created_at).filter(LogLine.service_id == service_id)
        if source_label:
            q = q.filter(LogLine.source_label == source_label)
        return q.order_by(LogLine.created_at.desc(), LogLine.id.desc()).limit(1).scalar()


def since_for_gap_fill(last: datetime | None, buffer_sec: int = 120) -> str:
    """Relative since accepted by Dokploy WS (all | Ns|m|h|d)."""
    if last is None:
        return "all"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if last.tzinfo is not None:
        last = last.replace(tzinfo=None)
    gap = max(0, (now - last).total_seconds())
    total = int(math.ceil(gap + buffer_sec))
    if total <= 60:
        return "1m"
    if total <= 3600:
        minutes = max(1, (total + 59) // 60)
        return f"{minutes}m"
    if total <= 86400:
        hours = max(1, (total + 3599) // 3600)
        return f"{hours}h"
    days = min(30, max(1, (total + 86399) // 86400))
    return f"{days}d"


def process_stream_chunk(
    service_id: int,
    source_label: str,
    chunk: str,
    line_buffer: str,
) -> tuple[str, int]:
    """Parse WS chunks into complete lines; ingest synchronously."""
    line_buffer += chunk
    added = 0
    while True:
        newline = line_buffer.find("\n")
        if newline < 0:
            break
        line = line_buffer[:newline]
        line_buffer = line_buffer[newline + 1:]
        if not line.strip():
            continue
        with SessionLocal() as session:
            settings = get_settings(session)
            service = session.get(Service, service_id)
            if service is None:
                return line_buffer, added
            n = ingest_log_line(session, settings, service, line, source_label)
            if n:
                trim_service_logs(session, service.id, settings.max_lines_per_service)
                service.last_fetch_at = datetime.now(timezone.utc).replace(tzinfo=None)
                service.last_error = None
            session.commit()
            added += n
    return line_buffer, added


def flush_line_buffer(service_id: int, source_label: str, line_buffer: str) -> int:
    if not line_buffer.strip():
        return 0
    with SessionLocal() as session:
        settings = get_settings(session)
        service = session.get(Service, service_id)
        if service is None:
            return 0
        n = ingest_log_line(session, settings, service, line_buffer, source_label)
        if n:
            trim_service_logs(session, service.id, settings.max_lines_per_service)
            service.last_fetch_at = datetime.now(timezone.utc).replace(tzinfo=None)
        session.commit()
        return n
