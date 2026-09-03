from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

from websocket import WebSocketTimeoutException, create_connection

from app.crypto import decrypt_secret
from app.db import SessionLocal, Service, Settings, get_settings
from app.dokploy import DokployClient, DokployError, _optional_id
from app.log_ingest import flush_line_buffer, last_seen_at, process_stream_chunk, since_for_gap_fill

logger = logging.getLogger(__name__)

RECONNECT_MIN_SEC = 2.0
RECONNECT_MAX_SEC = 60.0
DISCOVERY_DEFAULT_SEC = 60


@dataclass(frozen=True)
class StreamTarget:
    key: str
    service_id: int
    service_name: str
    container_id: str
    source_label: str
    server_id: str | None
    run_type: str
    dokploy_service_id: str | None
    tail: int
    initial_since: str


def _client_from_settings(settings: Settings) -> DokployClient | None:
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


def build_log_ws_url(
    base_url: str,
    container_id: str,
    tail: int,
    since: str,
    run_type: str,
    server_id: str | None = None,
    dokploy_service_id: str | None = None,
) -> str:
    http = base_url.rstrip("/")
    ws_base = http.replace("https://", "wss://").replace("http://", "ws://")
    query: dict[str, str] = {
        "containerId": container_id,
        "tail": str(tail),
        "since": since or "all",
        "runType": run_type,
    }
    if server_id:
        query["serverId"] = server_id
    if dokploy_service_id:
        query["serviceId"] = dokploy_service_id
    return f"{ws_base}/docker-container-logs?{urlencode(query, quote_via=quote)}"


def _running_containers(containers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for container in containers:
        cid = container.get("containerId") or container.get("id") or ""
        if not cid:
            continue
        state = (container.get("state") or "").lower()
        if state in ("", "running", "restarting"):
            result.append(container)
    if not result:
        for container in containers:
            cid = container.get("containerId") or ""
            if cid:
                result.append(container)
    return result


def resolve_stream_targets(
    client: DokployClient,
    settings: Settings,
    services: list[Service],
) -> list[StreamTarget]:
    targets: list[StreamTarget] = []
    tail = max(1, min(settings.log_tail or 300, 10000))
    cold_since = (settings.log_since or "all").strip() or "all"
    if cold_since != "all" and not (
        cold_since == "all" or (len(cold_since) >= 2 and cold_since[-1] in "smhd" and cold_since[:-1].isdigit())
    ):
        cold_since = "all"

    for service in services:
        if not service.enabled:
            continue
        if (
            settings.self_application_id
            and service.application_id
            and service.application_id == settings.self_application_id
        ):
            continue

        try:
            if service.dokploy_type == "application":
                targets.extend(
                    _targets_for_application(client, settings, service, tail, cold_since)
                )
            elif service.dokploy_type == "compose":
                targets.extend(
                    _targets_for_compose(client, settings, service, tail, cold_since)
                )
        except DokployError as exc:
            logger.warning("Resolve targets failed for %s: %s", service.name, exc)
            with SessionLocal() as session:
                row = session.get(Service, service.id)
                if row:
                    row.last_error = str(exc)[:2000]
                    session.commit()
    return targets


def _targets_for_application(
    client: DokployClient,
    settings: Settings,
    service: Service,
    tail: int,
    cold_since: str,
) -> list[StreamTarget]:
    details = client.application_one(service.application_id)
    app_name = details.get("appName") or service.app_name or ""
    server_id = _optional_id(details.get("serverId"))
    if app_name and app_name != service.app_name:
        with SessionLocal() as session:
            row = session.get(Service, service.id)
            if row:
                row.app_name = app_name
                session.commit()

    containers = client.containers_by_app_name(app_name, server_id=server_id) if app_name else []
    running = _running_containers(containers)

    if not running and app_name:
        label = service.name
        key = f"{service.id}:{app_name}:swarm"
        return [
            StreamTarget(
                key=key,
                service_id=service.id,
                service_name=service.name,
                container_id=app_name,
                source_label=label,
                server_id=server_id,
                run_type="swarm",
                dokploy_service_id=service.application_id,
                tail=tail,
                initial_since=cold_since,
            )
        ]

    out: list[StreamTarget] = []
    for container in running:
        cid = container.get("containerId") or container.get("id") or ""
        name = container.get("name") or cid
        label = f"{service.name} / {name}" if len(running) > 1 else service.name
        key = f"{service.id}:{cid}:native"
        out.append(
            StreamTarget(
                key=key,
                service_id=service.id,
                service_name=service.name,
                container_id=cid,
                source_label=label,
                server_id=server_id,
                run_type="native",
                dokploy_service_id=service.application_id,
                tail=tail,
                initial_since=cold_since,
            )
        )
    return out


def _targets_for_compose(
    client: DokployClient,
    _settings: Settings,
    service: Service,
    tail: int,
    cold_since: str,
) -> list[StreamTarget]:
    app_name = service.app_name
    compose_type = service.compose_type or "docker-compose"
    details = client.compose_one(service.compose_id)
    if not app_name:
        app_name = details.get("appName") or ""
        compose_type = details.get("composeType") or compose_type
    else:
        compose_type = details.get("composeType") or compose_type

    server_id = _optional_id(details.get("serverId"))
    run_type = "swarm" if compose_type == "stack" else "native"
    app_type = "stack" if run_type == "swarm" else "docker-compose"

    if app_name != service.app_name or compose_type != service.compose_type:
        with SessionLocal() as session:
            row = session.get(Service, service.id)
            if row:
                row.app_name = app_name
                row.compose_type = compose_type
                session.commit()

    if not app_name:
        raise DokployError(f"No appName for compose {service.compose_id}")

    containers = client.containers_by_app_name(app_name, app_type, server_id)
    running = _running_containers(containers)

    if not running:
        raise DokployError("No running containers for compose service")

    out: list[StreamTarget] = []
    for container in running:
        cid = container.get("containerId") or container.get("id") or ""
        name = container.get("name") or cid
        label = f"{service.name} / {name}"
        key = f"{service.id}:{cid}:{run_type}"
        out.append(
            StreamTarget(
                key=key,
                service_id=service.id,
                service_name=service.name,
                container_id=cid,
                source_label=label,
                server_id=server_id,
                run_type=run_type,
                dokploy_service_id=service.compose_id,
                tail=tail,
                initial_since=cold_since,
            )
        )
    with SessionLocal() as session:
        row = session.get(Service, service.id)
        if row and running:
            row.container_id = running[0].get("containerId") or running[0].get("id") or ""
            session.commit()
    return out


class _StreamWorker(threading.Thread):
    def __init__(
        self,
        target: StreamTarget,
        base_url: str,
        api_key: str,
        stop_event: threading.Event,
    ):
        super().__init__(name=f"log-stream-{target.key}", daemon=True)
        self.target = target
        self.base_url = base_url
        self.api_key = api_key
        self.stop_event = stop_event
        self._backoff = RECONNECT_MIN_SEC

    def run(self) -> None:
        while not self.stop_event.is_set():
            last = last_seen_at(self.target.service_id, self.target.source_label)
            if last is None:
                since = self.target.initial_since
                tail = self.target.tail
            else:
                since = since_for_gap_fill(last)
                tail = min(self.target.tail, 500)

            url = build_log_ws_url(
                self.base_url,
                self.target.container_id,
                tail,
                since,
                self.target.run_type,
                self.target.server_id,
                self.target.dokploy_service_id,
            )
            logger.info(
                "WS connect %s (%s) tail=%s since=%s",
                self.target.service_name,
                self.target.source_label,
                tail,
                since,
            )
            ws = None
            line_buffer = ""
            try:
                ws = create_connection(
                    url,
                    header=[f"x-api-key: {self.api_key}"],
                    timeout=90.0,
                )
                ws.settimeout(90.0)
                self._backoff = RECONNECT_MIN_SEC

                while not self.stop_event.is_set():
                    try:
                        message = ws.recv()
                    except WebSocketTimeoutException:
                        continue
                    if not message:
                        break
                    if isinstance(message, bytes):
                        message = message.decode("utf-8", "replace")
                    if message.startswith("This feature is not available"):
                        raise DokployError(message)
                    line_buffer, added = process_stream_chunk(
                        self.target.service_id,
                        self.target.source_label,
                        message,
                        line_buffer,
                    )
                    if added:
                        logger.debug(
                            "Ingested %s new lines for %s",
                            added,
                            self.target.source_label,
                        )
            except Exception as exc:  # noqa: BLE001
                if not self.stop_event.is_set():
                    logger.warning(
                        "WS error %s (%s): %s",
                        self.target.service_name,
                        self.target.source_label,
                        exc,
                    )
                    with SessionLocal() as session:
                        row = session.get(Service, self.target.service_id)
                        if row:
                            row.last_error = str(exc)[:2000]
                            session.commit()
            finally:
                if line_buffer:
                    flush_line_buffer(
                        self.target.service_id,
                        self.target.source_label,
                        line_buffer,
                    )
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:  # noqa: BLE001
                        pass

            if self.stop_event.wait(self._backoff):
                break
            self._backoff = min(self._backoff * 2, RECONNECT_MAX_SEC)


class LogStreamManager:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._supervisor: threading.Thread | None = None
        self._workers: dict[str, tuple[_StreamWorker, threading.Event]] = {}
        self._lock = threading.Lock()
        self._config_sig: str = ""

    def start(self) -> None:
        if self._supervisor and self._supervisor.is_alive():
            return
        self._stop.clear()
        self._supervisor = threading.Thread(target=self._supervisor_loop, name="log-stream-supervisor", daemon=True)
        self._supervisor.start()
        logger.info("Log stream manager started")

    def stop(self) -> None:
        self._stop.set()
        self._stop_all_workers()
        if self._supervisor:
            self._supervisor.join(timeout=15)
        logger.info("Log stream manager stopped")

    def sync_now(self) -> dict[str, Any]:
        try:
            self._sync_workers()
            return {"status": "ok", "streams": len(self._workers)}
        except Exception as exc:  # noqa: BLE001
            logger.exception("Stream sync failed")
            return {"status": "error", "detail": str(exc)}

    def _supervisor_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._sync_workers()
            except Exception:
                logger.exception("Supervisor sync failed")
            interval = DISCOVERY_DEFAULT_SEC
            with SessionLocal() as session:
                settings = get_settings(session)
                interval = max(15, settings.poll_interval_sec or DISCOVERY_DEFAULT_SEC)
            if self._stop.wait(interval):
                break

    def _config_signature(self, settings: Settings, enabled_ids: list[int]) -> str:
        return "|".join(
            [
                settings.dokploy_url or "",
                settings.dokploy_api_key_enc or "",
                str(settings.log_tail),
                settings.log_since or "all",
                settings.self_application_id or "",
                ",".join(str(i) for i in sorted(enabled_ids)),
            ]
        )

    def _sync_workers(self) -> None:
        with SessionLocal() as session:
            settings = get_settings(session)
            client = _client_from_settings(settings)
            services = session.query(Service).order_by(Service.name).all()
            enabled_ids = [s.id for s in services if s.enabled]
            new_sig = self._config_signature(settings, enabled_ids)

        if client is None:
            self._stop_all_workers()
            return

        force_rebuild = new_sig != self._config_sig
        if force_rebuild:
            logger.info("Stream settings changed, rebuilding all workers")
            self._stop_all_workers()
            self._config_sig = new_sig

        desired = resolve_stream_targets(client, settings, services)
        desired_map = {t.key: t for t in desired}
        desired_keys = set(desired_map.keys())

        with self._lock:
            for key in list(self._workers.keys()):
                if key not in desired_keys:
                    worker, ev = self._workers.pop(key)
                    ev.set()
                    logger.info("Stopped stream %s", key)

            for key, target in desired_map.items():
                if key in self._workers:
                    continue
                ev = threading.Event()
                worker = _StreamWorker(target, settings.dokploy_url, client.api_key, ev)
                self._workers[key] = (worker, ev)
                worker.start()
                logger.info("Started stream %s -> %s", target.service_name, target.source_label)

    def _stop_all_workers(self) -> None:
        with self._lock:
            for key, (worker, ev) in list(self._workers.items()):
                ev.set()
            for key, (worker, ev) in list(self._workers.items()):
                worker.join(timeout=5)
            self._workers.clear()
        self._config_sig = ""


_stream_manager: LogStreamManager | None = None


def get_stream_manager() -> LogStreamManager:
    global _stream_manager
    if _stream_manager is None:
        _stream_manager = LogStreamManager()
    return _stream_manager
