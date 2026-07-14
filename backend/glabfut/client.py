from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from glabflow import Client
from glabflow.graphql.builder import query as build_query

log = logging.getLogger("glabfut.client")
_CONTRIBUTOR_FETCH_CONCURRENCY = max(1, int(os.getenv("GLABFUT_CONTRIBUTOR_FETCH_CONCURRENCY", "8")))


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


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def _coerce_project(project: dict[str, Any]) -> dict[str, Any]:
    project_id = _parse_id(project.get("id")) or project.get("id")
    full_path = (
        project.get("fullPath")
        or project.get("pathWithNamespace")
        or project.get("path_with_namespace")
        or project.get("full_path")
    )
    normalized = dict(project)
    if project_id is not None:
        normalized["id"] = project_id
    if full_path:
        normalized["fullPath"] = full_path
        normalized["pathWithNamespace"] = full_path
    return normalized


def _merge_nodes(items: list[dict[str, Any]], key: str = "id") -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for item in items:
        node = dict(item)
        raw_key = node.get(key) or node.get("iid") or node.get("fullPath") or node.get("webUrl")
        if raw_key is None:
            continue
        node_id = str(raw_key)
        if node_id in merged:
            existing = merged[node_id]
            for field, value in node.items():
                if existing.get(field) in (None, "", [], {}):
                    existing[field] = value
            if node.get("_source") == "membership":
                existing["_source"] = "membership"
        else:
            merged[node_id] = node
    return list(merged.values())


def _normalize_mr(node: dict[str, Any], role: str) -> dict[str, Any]:
    mr = dict(node)
    project = mr.get("project") or {}
    author = mr.get("author") or {}
    mr["id"] = _parse_id(mr.get("id")) or mr.get("id")
    mr["project"] = _coerce_project(project) if isinstance(project, dict) else {}
    mr["author"] = author
    mr["role"] = role
    return mr


def _merge_role(existing_role: str | None, incoming_role: str | None) -> str:
    roles = {
        role
        for role in (
            str(existing_role or "").lower(),
            str(incoming_role or "").lower(),
        )
        if role
    }
    if not roles:
        return ""
    if len(roles) == 1:
        return next(iter(roles))
    return "_".join(sorted(roles))


def _normalize_issue(node: dict[str, Any], role: str) -> dict[str, Any]:
    issue = dict(node)
    project = issue.get("project") or {}
    issue["id"] = _parse_id(issue.get("id")) or issue.get("id")
    issue["project"] = _coerce_project(project) if isinstance(project, dict) else {}
    issue["role"] = role
    return issue


def _identity_terms(profile: dict[str, Any] | None, username: str) -> list[str]:
    values = [
        username,
        _safe_str((profile or {}).get("username")),
        _safe_str((profile or {}).get("name")),
        _safe_str((profile or {}).get("publicEmail")),
        _safe_str((profile or {}).get("commitEmail")),
        _safe_str((profile or {}).get("email")),
    ]
    seen: set[str] = set()
    terms: list[str] = []
    for value in values:
        normalized = value.lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            terms.append(value)
    return terms


def _match_contributor(contributor: dict[str, Any], terms: list[str]) -> bool:
    name = _safe_str(contributor.get("name")).lower()
    email = _safe_str(contributor.get("email")).lower()
    for term in terms:
        normalized = term.lower()
        if not normalized:
            continue
        if normalized == name or normalized == email:
            return True
        if "@" in normalized and email == normalized:
            return True
        if normalized in {name.replace(" ", ""), email.split("@")[0]}:
            return True
    return False


def _is_recent(iso_date: str | None, days: int = 365) -> bool:
    if not iso_date:
        return False
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        created = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
        return created >= cutoff
    except (ValueError, AttributeError):
        return False


def _push_commit_count(event: dict[str, Any]) -> int:
    payload = event.get("push_data") or event.get("pushData") or {}
    raw = payload.get("commit_count") or payload.get("commitCount") or 0
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def _q_user_projects(role: str) -> tuple[str, list[str]]:
    connection = "contributedProjects" if role == "contributed" else "projectMemberships"
    q = build_query(f"UserProjects{role.title()}").arg("username", "$username", "String!")
    q.arg("after", "$after", "String")
    q.arg("first", "$first", "Int")
    user = q.field("user", args={"username": "$username"})
    projects = user.field(connection, args={"after": "$after", "first": "$first"})
    projects.field("pageInfo", fields=["hasNextPage", "endCursor"])
    nodes = projects.field("nodes")
    if role == "contributed":
        project_node = nodes
    else:
        project_node = nodes.field("project")
    project_node.field("id")
    project_node.field("name")
    project_node.field("fullPath")
    project_node.field("webUrl")
    project_node.field("starCount")
    repo = project_node.field("repository")
    repo_langs = repo.field("languages")
    repo_langs.field("nodes", fields=["name", "share"])
    return q.build(), ["user", connection]


def _q_user_merge_requests(role: str) -> tuple[str, list[str]]:
    connection = "authoredMergeRequests" if role == "authored" else "assignedMergeRequests"
    q = build_query(f"UserMergeRequests{role.title()}").arg("username", "$username", "String!")
    q.arg("after", "$after", "String")
    q.arg("first", "$first", "Int")
    user = q.field("user", args={"username": "$username"})
    mrs = user.field(connection, args={"after": "$after", "first": "$first", "state": "all"})
    mrs.field("pageInfo", fields=["hasNextPage", "endCursor"])
    nodes = mrs.field("nodes")
    for field_name in (
        "id",
        "iid",
        "title",
        "state",
        "webUrl",
        "createdAt",
        "mergedAt",
        "updatedAt",
        "userNotesCount",
    ):
        nodes.field(field_name)
    nodes.field("author", fields=["username", "name"])
    nodes.field("reviewers").field("nodes", fields=["username", "name"])
    project = nodes.field("project")
    project.field("id")
    project.field("name")
    project.field("fullPath")
    commits = nodes.field("commits", args={"first": 100})
    commits.field("nodes", fields=["sha", "title", "authoredDate", "authorName", "authorEmail"])
    return q.build(), ["user", connection]


def _q_user_issues(role: str) -> tuple[str, list[str]]:
    q = build_query(f"UserIssues{role.title()}").arg("username", "$username", "String!")
    q.arg("after", "$after", "String")
    q.arg("first", "$first", "Int")
    if role == "authored":
        issues = q.field("issues", args={"after": "$after", "first": "$first", "authorUsername": "$username", "state": "all"})
        connection_path = ["issues"]
    else:
        user = q.field("user", args={"username": "$username"})
        issues = user.field("assignedIssues", args={"after": "$after", "first": "$first", "state": "all"})
        connection_path = ["user", "assignedIssues"]
    issues.field("pageInfo", fields=["hasNextPage", "endCursor"])
    nodes = issues.field("nodes")
    for field_name in ("id", "iid", "title", "state", "webUrl", "createdAt", "closedAt"):
        nodes.field(field_name)
    project = nodes.field("project")
    project.field("id")
    project.field("name")
    project.field("fullPath")
    return q.build(), connection_path


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

    async def _gql_exec(self, q: str, variables: dict[str, Any]) -> dict[str, Any] | None:
        try:
            gl = await self._get_client()
            return await gl.graphql.execute(q, variables=variables)
        except Exception as exc:
            log.warning("GQL execute failed: %s", exc)
            return None

    async def _gql_stream(
        self,
        q: str,
        connection_path: list[str],
        variables: dict[str, Any],
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        try:
            gl = await self._get_client()
            async for item in gl.graphql.stream(q, connection_path=connection_path, variables=variables):
                if isinstance(item, dict):
                    items.append(item)
        except Exception as exc:
            log.warning("GQL stream failed for %s: %s", ".".join(connection_path), exc)
        return items

    async def _rest_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            gl = await self._get_client()
            safe = {
                key: ("true" if value is True else "false" if value is False else value)
                for key, value in (params or {}).items()
                if value is not None
            }
            raw = await gl.get(f"/api/v4{path}", params=safe)
            decoded = _decode(raw) if isinstance(raw, bytes) else raw
            if isinstance(decoded, dict) and decoded.get("error"):
                log.warning("REST %s API error: %s", path, decoded.get("error"))
                return None
            return decoded
        except Exception as exc:
            log.warning("REST %s failed: %s", path, exc)
            return None

    async def _rest_paginate(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        try:
            gl = await self._get_client()
            safe = {
                key: ("true" if value is True else "false" if value is False else value)
                for key, value in (params or {}).items()
                if value is not None
            }
            async for page_bytes in gl.paginate(f"/api/v4{path}", params=safe):
                page = _decode(page_bytes)
                if isinstance(page, list):
                    items.extend(node for node in page if isinstance(node, dict))
        except Exception as exc:
            log.warning("Paginate %s failed: %s", path, exc)
        return items

    async def fetch_user_profile(self, username: str) -> dict[str, Any] | None:
        from glabflow.graphql import queries as qm

        result = await self._gql_exec(qm.get_user_details(), {"username": username})
        if result and result.get("user"):
            log.info("Profile: %s via GraphQL", username)
            return result["user"]

        for field in ("username", "search"):
            users = await self._rest_get("/users", {field: username})
            if isinstance(users, list) and users:
                target = next(
                    (
                        user
                        for user in users
                        if str(user.get("username", "")).lower() == username.lower()
                    ),
                    None,
                )
                if target:
                    log.info("Profile: %s via REST (%s)", username, field)
                    return target
        return None

    async def fetch_user_events(self, username: str, user_id: int | str | None = None) -> list[dict[str, Any]]:
        resolved_user_id = _parse_id(user_id) if user_id is not None else None
        if resolved_user_id is None and user_id is not None:
            resolved_user_id = user_id
        if not resolved_user_id:
            user = await self.fetch_user_profile(username)
            if not user or not user.get("id"):
                return []
            resolved_user_id = _parse_id(user["id"]) or user["id"]
        return await self._rest_paginate(f"/users/{resolved_user_id}/events", {"sort": "desc"})

    async def _fetch_project_languages(self, project: dict[str, Any]) -> dict[str, Any]:
        project_id = project.get("id")
        if not project_id:
            return []
        repository = project.get("repository") or {}
        existing = repository.get("languages") if isinstance(repository, dict) else None
        if isinstance(existing, dict) and existing.get("nodes"):
            return project
        langs = await self._rest_get(f"/projects/{project_id}/languages")
        if isinstance(langs, dict) and langs:
            nodes = [{"name": name, "share": share} for name, share in langs.items()]
            project["repository"] = {"languages": {"nodes": nodes}}
        return project

    async def fetch_user_projects(self, username: str) -> list[dict[str, Any]]:
        projects: list[dict[str, Any]] = []
        contributed_query, contributed_path = _q_user_projects("contributed")
        memberships_query, memberships_path = _q_user_projects("membership")
        contributed_nodes, membership_nodes = await asyncio.gather(
            self._gql_stream(contributed_query, contributed_path, {"username": username}),
            self._gql_stream(memberships_query, memberships_path, {"username": username}),
        )
        if contributed_nodes or membership_nodes:
            projects.extend(
                {
                    **_coerce_project(node),
                    "_source": "contributed",
                    "_contributed": True,
                }
                for node in contributed_nodes
                if isinstance(node, dict)
            )
            projects.extend(
                {
                    **_coerce_project(node.get("project") if isinstance(node.get("project"), dict) else node),
                    "_source": "membership",
                    "_member": True,
                }
                for node in membership_nodes
                if isinstance(node, dict)
            )
        else:
            profile = await self.fetch_user_profile(username)
            uid = _parse_id((profile or {}).get("id"))
            if uid:
                owned, contributed = await asyncio.gather(
                    self._rest_paginate(f"/users/{uid}/projects", {"simple": True, "membership": True}),
                    self._rest_paginate(f"/users/{uid}/contributed_projects", {"simple": True}),
                )
                projects.extend({**_coerce_project(node), "_source": "membership", "_member": True} for node in owned)
                projects.extend({**_coerce_project(node), "_source": "contributed", "_contributed": True} for node in contributed)

        deduped = _merge_nodes(projects)
        hydrated = await asyncio.gather(*(self._fetch_project_languages(project) for project in deduped))
        log.info("Projects: %s got %d unique projects", username, len(hydrated))
        return hydrated

    async def _fetch_user_mrs_rest(self, username: str, role: str) -> list[dict[str, Any]]:
        if role == "authored":
            params = {"author_username": username, "state": "all", "scope": "all"}
        else:
            params = {"reviewer_username": username, "state": "all", "scope": "all"}
        items = await self._rest_paginate("/merge_requests", params)
        normalized: list[dict[str, Any]] = []
        for node in items:
            project = {
                "id": node.get("project_id"),
                "fullPath": node.get("references", {}).get("full"),
                "name": node.get("references", {}).get("full"),
            }
            normalized.append(
                {
                    **node,
                    "project": _coerce_project(project),
                    "author": node.get("author") or {},
                    "role": role,
                    "reviewers": {"nodes": node.get("reviewers") or []},
                    "userNotesCount": node.get("user_notes_count") or 0,
                    "commits": {"nodes": []},
                }
            )
        return normalized

    async def _fetch_mr_notes(self, project_id: int | str | None, mr_iid: int | str | None) -> list[dict[str, Any]]:
        if not project_id or mr_iid is None:
            return []
        notes = await self._rest_paginate(f"/projects/{project_id}/merge_requests/{mr_iid}/notes")
        return [note for note in notes if isinstance(note, dict)]

    async def fetch_user_mrs(self, username: str) -> list[dict[str, Any]]:
        authored_query, authored_path = _q_user_merge_requests("authored")
        assigned_query, assigned_path = _q_user_merge_requests("assigned")
        authored_task = self._gql_stream(authored_query, authored_path, {"username": username})
        assigned_task = self._gql_stream(assigned_query, assigned_path, {"username": username})
        authored, assigned = await asyncio.gather(authored_task, assigned_task)

        items: list[dict[str, Any]] = []
        if authored:
            items.extend(_normalize_mr(node, "authored") for node in authored)
        if assigned:
            items.extend(_normalize_mr(node, "assigned") for node in assigned)
        if not items:
            authored_rest, assigned_rest = await asyncio.gather(
                self._fetch_user_mrs_rest(username, "authored"),
                self._fetch_user_mrs_rest(username, "assigned"),
            )
            items.extend(authored_rest)
            items.extend(assigned_rest)

        merged: dict[str, dict[str, Any]] = {}
        for item in items:
            key = str(item.get("id") or item.get("iid"))
            if key in merged:
                existing = merged[key]
                existing["role"] = _merge_role(existing.get("role"), item.get("role"))
                for field, value in item.items():
                    if existing.get(field) in (None, "", [], {}):
                        existing[field] = value
            else:
                merged[key] = item

        results = list(merged.values())
        note_semaphore = asyncio.Semaphore(_CONTRIBUTOR_FETCH_CONCURRENCY)

        async def _enrich_notes(mr: dict[str, Any]) -> None:
            if (mr.get("userNotesCount") or 0) <= 0:
                return
            project_id = (mr.get("project") or {}).get("id")
            iid = mr.get("iid")
            async with note_semaphore:
                mr["notes"] = await self._fetch_mr_notes(project_id, iid)

        await asyncio.gather(*(_enrich_notes(mr) for mr in results))
        log.info("MRs: %s got %d via GraphQL/REST", username, len(results))
        return results

    async def _fetch_user_issues_rest(self, username: str, role: str) -> list[dict[str, Any]]:
        params = {"state": "all", "scope": "all"}
        if role == "authored":
            params["author_username"] = username
        else:
            params["assignee_username"] = username
        items = await self._rest_paginate("/issues", params)
        normalized: list[dict[str, Any]] = []
        for node in items:
            project = {
                "id": node.get("project_id"),
                "fullPath": node.get("references", {}).get("full"),
                "name": node.get("references", {}).get("full"),
            }
            normalized.append(
                {
                    **node,
                    "project": _coerce_project(project),
                    "role": role,
                }
            )
        return normalized

    async def fetch_user_issues(self, username: str) -> list[dict[str, Any]]:
        authored_query, authored_path = _q_user_issues("authored")
        assigned_query, assigned_path = _q_user_issues("assigned")
        authored_task = self._gql_stream(authored_query, authored_path, {"username": username})
        assigned_task = self._gql_stream(assigned_query, assigned_path, {"username": username})
        authored, assigned = await asyncio.gather(authored_task, assigned_task)

        items: list[dict[str, Any]] = []
        if authored:
            items.extend(_normalize_issue(node, "authored") for node in authored)
        if assigned:
            items.extend(_normalize_issue(node, "assigned") for node in assigned)
        if not items:
            authored_rest, assigned_rest = await asyncio.gather(
                self._fetch_user_issues_rest(username, "authored"),
                self._fetch_user_issues_rest(username, "assigned"),
            )
            items.extend(authored_rest)
            items.extend(assigned_rest)

        merged: dict[str, dict[str, Any]] = {}
        for item in items:
            key = str(item.get("id") or item.get("iid"))
            if key in merged:
                existing = merged[key]
                existing["role"] = _merge_role(existing.get("role"), item.get("role"))
            else:
                merged[key] = item

        results = list(merged.values())
        log.info("Issues: %s got %d via GraphQL/REST", username, len(results))
        return results

    async def fetch_user_commit_stats(
        self,
        username: str,
        profile: dict[str, Any] | None,
        projects: list[dict[str, Any]],
        events: list[dict[str, Any]],
    ) -> dict[str, int]:
        recent_commits = 0
        pushed_commits_total = 0
        for event in events:
            action = _safe_str(event.get("action_name") or event.get("action"))
            if "push" not in action.lower():
                continue
            commit_count = _push_commit_count(event)
            pushed_commits_total += commit_count
            created = event.get("created_at") or event.get("createdAt")
            if _is_recent(created):
                recent_commits += commit_count

        contributor_results: list[tuple[bool, list[dict[str, Any]] | None]] = []
        semaphore = asyncio.Semaphore(_CONTRIBUTOR_FETCH_CONCURRENCY)

        async def _fetch_project_contributors(project: dict[str, Any]) -> tuple[bool, list[dict[str, Any]] | None]:
            project_id = project.get("id")
            if not project_id:
                return False, None
            is_external = bool(project.get("_contributed")) and not bool(project.get("_member"))
            async with semaphore:
                contributors = await self._rest_paginate(f"/projects/{project_id}/repository/contributors")
            return is_external, contributors

        if projects:
            contributor_results = await asyncio.gather(*(_fetch_project_contributors(project) for project in projects))
        identity_terms = _identity_terms(profile, username)

        contributor_commits_total = 0
        commits_to_others = 0
        for is_external, contributors in contributor_results:
            if not isinstance(contributors, list):
                continue
            for contributor in contributors:
                if not isinstance(contributor, dict) or not _match_contributor(contributor, identity_terms):
                    continue
                commit_total = contributor.get("commits") or 0
                try:
                    commit_total = max(0, int(commit_total))
                except (TypeError, ValueError):
                    commit_total = 0
                contributor_commits_total += commit_total
                if is_external:
                    commits_to_others += commit_total
        lifetime_commits = max(pushed_commits_total, contributor_commits_total)

        return {
            "recent_commits": recent_commits,
            "lifetime_commits": max(recent_commits, lifetime_commits),
            "commits_to_others": commits_to_others,
        }

    async def close(self) -> None:
        if self._client:
            await self._client.__aexit__(None, None, None)
            self._client = None
            log.info("Client closed")
