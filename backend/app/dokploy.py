from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlencode, urljoin

import httpx

TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class DokployError(RuntimeError):
    pass


def _unwrap(data: Any) -> Any:
    if isinstance(data, dict) and "result" in data:
        result = data["result"]
        if isinstance(result, dict) and "data" in result:
            inner = result["data"]
            if isinstance(inner, dict) and "json" in inner and len(inner) <= 2:
                return inner["json"]
            return inner
        return result
    return data


class DokployClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/") + "/"
        self.api_key = api_key

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "accept": "application/json",
        }

    def _url(self, path: str) -> str:
        return urljoin(self.base_url, path.lstrip("/"))

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        query = {k: v for k, v in (params or {}).items() if v is not None}
        url = self._url(path)
        if query:
            url = f"{url}?{urlencode(query, quote_via=quote)}"
        try:
            with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
                response = client.get(url, headers=self._headers())
        except httpx.HTTPError as exc:
            raise DokployError(f"Dokploy request failed: {exc}") from exc
        if response.status_code >= 400:
            body = response.text[:500]
            raise DokployError(f"Dokploy {response.status_code} for {path}: {body}")
        if not response.content:
            return None
        try:
            return _unwrap(response.json())
        except ValueError:
            return response.text

    def project_all(self) -> list[dict[str, Any]]:
        data = self.get("/api/project.all")
        if not isinstance(data, list):
            raise DokployError("Unexpected project.all response")
        return data

    def compose_one(self, compose_id: str) -> dict[str, Any]:
        data = self.get("/api/compose.one", {"composeId": compose_id})
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
        data = self.get("/api/docker.getContainersByAppNameMatch", params)
        if data is None:
            return []
        if not isinstance(data, list):
            raise DokployError("Unexpected docker.getContainersByAppNameMatch response")
        return data

    def application_read_logs(
        self, application_id: str, tail: int, since: str
    ) -> str:
        data = self.get(
            "/api/application.readLogs",
            {
                "applicationId": application_id,
                "tail": tail,
                "since": since,
            },
        )
        return data if isinstance(data, str) else str(data or "")

    def compose_read_logs(
        self, compose_id: str, container_id: str, tail: int, since: str
    ) -> str:
        data = self.get(
            "/api/compose.readLogs",
            {
                "composeId": compose_id,
                "containerId": container_id,
                "tail": tail,
                "since": since,
            },
        )
        return data if isinstance(data, str) else str(data or "")


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
