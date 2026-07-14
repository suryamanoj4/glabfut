from __future__ import annotations

import json
import logging
import os
from typing import Any

from glabflow import Client
from glabflow.graphql.builder import query as build_query

log = logging.getLogger("glabfut.client")


def _parse_id(gid: str | int | None) -> int | None:
    if gid is None:
        return None
    if isinstance(gid, int):
        return gid
    try:
        return int(gid.split("/")[-1])
    except (ValueError, IndexError, AttributeError):
        return None


def _decode(data: bytes) -> Any:
    try:
        return json.loads(data)
    except (ValueError, TypeError):
        return None


# ── Custom simplified GraphQL queries (only universal fields) ──

def _q_user_projects():
    q = build_query("UserProjects").arg("username", "$username", "String!")
    u = q.field("user", args={"username": "$username"})
    cp = u.field("contributedProjects", args={"first": 100})
    cp.field("nodes", fields=["id", "name", "fullPath", "webUrl"])
    pm = u.field("projectMemberships", args={"first": 100})
    pm.field("nodes").field("project", fields=["id", "name", "fullPath", "webUrl"])
    return q.build()


def _q_user_mrs():
    q = build_query("UserMRs").arg("username", "$username", "String!")
    u = q.field("user", args={"username": "$username"})
    mrs = u.field("authoredMergeRequests", args={"first": 100})
    mrs.field("pageInfo", fields=["hasNextPage", "endCursor"])
    mrs.field("nodes", fields=["id", "iid", "title", "state", "webUrl", "createdAt", "mergedAt"])
    return q.build()


def _q_user_issues():
    q = build_query("UserIssues").arg("username", "$username", "String!")
    mrs = q.field("issues", args={"first": 100, "authorUsername": "$username", "state": "all"})
    mrs.field("pageInfo", fields=["hasNextPage", "endCursor"])
    mrs.field("nodes", fields=["id", "iid", "title", "state", "webUrl", "createdAt", "closedAt"])
    return q.build()


class GitLabProfileClient:
    def __init__(self, gitlab_url: str | None = None, token: str | None = None):
        self._gitlab_url = gitlab_url or os.getenv("GITLAB_URL", "https://gitlab.com")
        self._token = token or os.getenv("GITLAB_TOKEN") or ""
        self._client: Client | None = None

    async def _get_client(self) -> Client:
        if self._client is None:
            self._client = Client(self._gitlab_url, self._token, safe_mode=True)
            await self._client.__aenter__()
            log.info("Client opened: url=%s safe_mode=True", self._gitlab_url)
        return self._client

    # ── GraphQL (works on all instances) ──

    async def _gql_exec(self, q, variables: dict) -> dict | None:
        try:
            gl = await self._get_client()
            return await gl.graphql.execute(q, variables=variables)
        except Exception as e:
            log.warning("GQL failed: %s", e)
            return None

    async def _gql_stream(self, q, conn_path: list[str], variables: dict):
        items = []
        try:
            gl = await self._get_client()
            async for item in gl.graphql.stream(q, connection_path=conn_path, variables=variables):
                items.append(item)
        except Exception as e:
            log.warning("GQL stream failed: %s", e)
        return items

    # ── REST fallback (paginated) ──

    async def _rest_get(self, path: str, params: dict | None = None) -> Any:
        try:
            gl = await self._get_client()
            safe = {k: ("true" if v is True else "false" if v is False else v)
                    for k, v in (params or {}).items() if v is not None}
            raw = await gl.get(f"/api/v4{path}", params=safe)
            decoded = _decode(raw) if isinstance(raw, bytes) else raw
            if isinstance(decoded, dict) and decoded.get("error"):
                log.warning("REST %s API error: %s", path, decoded.get("error"))
                return None
            return decoded
        except Exception as e:
            log.warning("REST %s failed: %s", path, e)
            return None

    async def _rest_paginate(self, path: str, params: dict | None = None) -> list[dict]:
        items = []
        try:
            gl = await self._get_client()
            safe = {k: ("true" if v is True else "false" if v is False else v)
                    for k, v in (params or {}).items() if v is not None}
            async for page_bytes in gl.paginate(f"/api/v4{path}", params=safe):
                page = _decode(page_bytes)
                if isinstance(page, list):
                    items.extend(page)
        except Exception as e:
            log.warning("Paginate %s failed: %s", path, e)
        return items

    # ── Data fetchers: GraphQL → REST fallback ──

    async def fetch_user_profile(self, username: str) -> dict[str, Any] | None:
        from glabflow.graphql import queries as qm
        result = await self._gql_exec(qm.get_user_details(), {"username": username})
        if result and result.get("user"):
            log.info("Profile: %s via GraphQL", username)
            return result["user"]
        for field in ("username", "search"):
            users = await self._rest_get("/users", {field: username})
            if isinstance(users, list) and users:
                target = next((u for u in users if str(u.get("username", "")).lower() == username.lower()), None)
                if target:
                    log.info("Profile: %s via REST (%s)", username, field)
                    return target
        return None

    async def fetch_user_events(self, username: str) -> list[dict]:
        user = await self.fetch_user_profile(username)
        if not user or not user.get("id"):
            return []
        return await self._rest_paginate(f"/users/{_parse_id(user['id']) or user['id']}/events")

    async def fetch_user_projects(self, username: str) -> list[dict]:
        result = await self._gql_exec(_q_user_projects(), {"username": username})
        if result:
            user = result.get("user", {})
            contributed = (user.get("contributedProjects") or {}).get("nodes") or []
            memberships = (user.get("projectMemberships") or {}).get("nodes") or []
            projects = [m["project"] for m in memberships if isinstance(m, dict) and "project" in m]
            projects.extend(contributed)
            log.info("Projects: %s got %d via GraphQL", username, len(projects))
            return projects
        uid = _parse_id((await self.fetch_user_profile(username) or {}).get("id"))
        if uid:
            return await self._rest_paginate(f"/users/{uid}/projects")
        return []

    async def fetch_user_mrs(self, username: str) -> list[dict]:
        mrs = await self._gql_stream(_q_user_mrs(), ["user", "authoredMergeRequests"], {"username": username})
        if mrs:
            log.info("MRs: %s got %d via GraphQL", username, len(mrs))
            return mrs
        mrs = await self._rest_paginate("/merge_requests", {"author_username": username, "state": "all", "scope": "all"})
        log.info("MRs: %s got %d via REST", username, len(mrs))
        return mrs

    async def fetch_user_issues(self, username: str) -> list[dict]:
        issues = await self._gql_stream(_q_user_issues(), ["issues"], {"username": username})
        if issues:
            log.info("Issues: %s got %d via GraphQL", username, len(issues))
            return issues
        issues = await self._rest_paginate("/issues", {"author_username": username, "state": "all", "scope": "all"})
        log.info("Issues: %s got %d via REST", username, len(issues))
        return issues

    async def close(self):
        if self._client:
            await self._client.__aexit__(None, None, None)
            self._client = None
            log.info("Client closed")
