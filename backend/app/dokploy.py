from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import quote, urlencode, urljoin

import httpx

TIMEOUT = httpx.Timeout(30.0, connect=10.0)
logger = logging.getLogger(__name__)


class DokployError(RuntimeError):
    pass


def _unwrap(data: Any) -> Any:
    if isinstance(data, dict) and "0" in data:
        data = data["0"]
    if isinstance(data, dict) and "error" in data:
        err = data["error"]
        if isinstance(err, dict):
            inner = err.get("json") if isinstance(err.get("json"), dict) else err
            message = inner.get("message") or inner.get("code") or "Dokploy error"
            raise DokployError(str(message))
    if isinstance(data, dict) and "result" in data:
        result = data["result"]
        if isinstance(result, dict) and "data" in result:
            inner = result["data"]
            if isinstance(inner, dict) and "json" in inner and len(inner) <= 2:
                return inner["json"]
            return inner
        return result
    return data


def _response_message(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text[:500]
    try:
        _unwrap(data)
    except DokployError as exc:
        return str(exc)
    if isinstance(data, dict):
        return str(data.get("message") or data.get("code") or data)[:500]
    return response.text[:500]


def _retriable(exc: DokployError) -> bool:
    text = str(exc)
    return any(
        token in text
        for token in (
            " 400 ",
            " 404 ",
            "NOT_FOUND",
            "Not found",
            "BAD_REQUEST",
            "expected object",
            "No procedure",
            "No \"query\"-procedure",
        )
    )


def _json_params(params: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in params.items():
        if key == "tail":
            out[key] = int(value)
        else:
            out[key] = value
    return out


class DokployClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/") + "/"
        self.api_key = api_key

    def _headers(self, json_body: bool = False) -> dict[str, str]:
        headers = {
            "x-api-key": self.api_key,
            "accept": "application/json",
        }
        if json_body:
            headers["content-type"] = "application/json"
        return headers

    def _url(self, path: str) -> str:
        return urljoin(self.base_url, path.lstrip("/"))

    def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
    ) -> Any:
        url = self._url(path)
        query = {k: v for k, v in (params or {}).items() if v is not None}
        if query and json_body is None and method == "GET":
            url = f"{url}?{urlencode(query, quote_via=quote)}"
            query = None
        try:
            with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
                response = client.request(
                    method,
                    url,
                    headers=self._headers(json_body is not None),
                    params=query,
                    json=json_body,
                )
        except httpx.HTTPError as exc:
            raise DokployError(f"Dokploy request failed: {exc}") from exc
        if response.status_code >= 400:
            raise DokployError(
                f"Dokploy {response.status_code} for {path}: {_response_message(response)}"
            )
        if not response.content:
            return None
        try:
            return _unwrap(response.json())
        except DokployError:
            raise
        except ValueError:
            return response.text

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._send("GET", path, params=params)

    def query(self, procedure: str, params: dict[str, Any] | None = None) -> Any:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        last_error: DokployError | None = None
        attempts: list[tuple[str, dict[str, Any]]] = []

        try:
            return self.get(f"/api/{procedure}", params)
        except DokployError as exc:
            last_error = exc
            if not _retriable(exc):
                raise

        payload = _json_params(params)
        encoded = quote(json.dumps({"json": payload}, separators=(",", ":")), safe="")
        batch = quote(
            json.dumps({"0": {"json": payload}}, separators=(",", ":")),
            safe="",
        )
        attempts = [
            ("GET", {"path": f"/api/trpc/{procedure}?input={encoded}"}),
            ("GET", {"path": f"/api/trpc/{procedure}?batch=1&input={batch}"}),
            ("POST", {"path": f"/api/trpc/{procedure}", "json_body": {"json": payload}}),
        ]
        for method, kwargs in attempts:
            try:
                return self._send(method, kwargs["path"], json_body=kwargs.get("json_body"))
            except DokployError as exc:
                last_error = exc
                if not _retriable(exc):
                    raise
        raise last_error or DokployError(f"Dokploy query failed: {procedure}")

    def project_all(self) -> list[dict[str, Any]]:
        data = self.query("project.all")
        if not isinstance(data, list):
            raise DokployError("Unexpected project.all response")
        return data

    def application_one(self, application_id: str) -> dict[str, Any]:
        data = self.query("application.one", {"applicationId": application_id})
        if not isinstance(data, dict):
            raise DokployError("Unexpected application.one response")
        return data

    def compose_one(self, compose_id: str) -> dict[str, Any]:
        data = self.query("compose.one", {"composeId": compose_id})
        if not isinstance(data, dict):
            raise DokployError("Unexpected compose.one response")
        return data

    def containers_by_app_name(
        self,
        app_name: str,
        app_type: str | None = None,
        server_id: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"appName": app_name}
        if app_type:
            params["appType"] = app_type
        if server_id:
            params["serverId"] = server_id
        data = self.query("docker.getContainersByAppNameMatch", params)
        if data is None:
            return []
        if not isinstance(data, list):
            raise DokployError("Unexpected docker.getContainersByAppNameMatch response")
        return data


def _optional_id(value: Any) -> str | None:
    if not value or value in ("local", "null"):
        return None
    return str(value)


def dashboard_application_path(
    project_id: str, environment_id: str, application_id: str
) -> str:
    return (
        f"/dashboard/project/{project_id}/environment/{environment_id}"
        f"/services/application/{application_id}?tab=logs"
    )


def dashboard_compose_path(
    project_id: str, environment_id: str, compose_id: str
) -> str:
    return (
        f"/dashboard/project/{project_id}/environment/{environment_id}"
        f"/services/compose/{compose_id}?tab=logs"
    )


def list_project_services(projects: list[dict[str, Any]], project_id: str) -> list[dict[str, Any]]:
    found = next((p for p in projects if p.get("projectId") == project_id), None)
    if not found:
        raise DokployError(f"Project {project_id} not found")

    items: list[dict[str, Any]] = []
    environments = found.get("environments") or []
    if environments:
        for env in environments:
            env_id = env.get("environmentId") or ""
            for app in env.get("applications") or []:
                app_id = app.get("applicationId") or ""
                items.append(
                    {
                        "external_key": f"application:{app_id}",
                        "dokploy_type": "application",
                        "application_id": app_id,
                        "compose_id": "",
                        "name": app.get("name") or app_id,
                        "project_id": project_id,
                        "environment_id": env_id,
                        "dokploy_path": dashboard_application_path(
                            project_id, env_id, app_id
                        ),
                    }
                )
            for compose in env.get("compose") or []:
                compose_id = compose.get("composeId") or ""
                items.append(
                    {
                        "external_key": f"compose:{compose_id}",
                        "dokploy_type": "compose",
                        "application_id": "",
                        "compose_id": compose_id,
                        "name": compose.get("name") or compose_id,
                        "project_id": project_id,
                        "environment_id": env_id,
                        "dokploy_path": dashboard_compose_path(
                            project_id, env_id, compose_id
                        ),
                    }
                )
        return items

    env_id = found.get("environmentId") or ""
    for app in found.get("applications") or []:
        app_id = app.get("applicationId") or ""
        env = app.get("environmentId") or env_id
        items.append(
            {
                "external_key": f"application:{app_id}",
                "dokploy_type": "application",
                "application_id": app_id,
                "compose_id": "",
                "name": app.get("name") or app_id,
                "project_id": project_id,
                "environment_id": env,
                "dokploy_path": dashboard_application_path(project_id, env, app_id),
            }
        )
    for compose in found.get("compose") or []:
        compose_id = compose.get("composeId") or ""
        env = compose.get("environmentId") or env_id
        items.append(
            {
                "external_key": f"compose:{compose_id}",
                "dokploy_type": "compose",
                "application_id": "",
                "compose_id": compose_id,
                "name": compose.get("name") or compose_id,
                "project_id": project_id,
                "environment_id": env,
                "dokploy_path": dashboard_compose_path(project_id, env, compose_id),
            }
        )
    return items


def flatten_projects(projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for project in projects:
        env_names = [e.get("name") for e in (project.get("environments") or []) if e.get("name")]
        result.append(
            {
                "projectId": project.get("projectId"),
                "name": project.get("name") or project.get("projectId"),
                "environments": env_names,
            }
        )
    return result
