from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from .types import Signals

log = logging.getLogger("glabfut.signals")


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v) if v is not None else default
    except (ValueError, TypeError):
        return default


def _safe_str(v: Any, default: str | None = None) -> str | None:
    return str(v) if v else default


def _years_since(iso_date: str | None) -> float:
    if not iso_date:
        return 0.0
    try:
        created = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - created
        return max(0.0, delta.days / 365.25)
    except (ValueError, AttributeError):
        return 0.0


def _gql_or_rest(obj: dict, gql_key: str, rest_key: str, default: Any = None) -> Any:
    return obj.get(gql_key) or obj.get(rest_key) or default


def _nested_count(obj: dict, *paths: str) -> int:
    val = obj
    for key in paths:
        if isinstance(val, dict):
            val = val.get(key, {})
        else:
            return 0
    return _safe_int(val if isinstance(val, (int, float)) else len(val) if isinstance(val, (list, dict)) else 0)


def _list_nodes(obj: dict, key: str, nodes_key: str = "nodes") -> list:
    v = obj.get(key, [])
    if isinstance(v, dict):
        return v.get(nodes_key, []) if isinstance(v.get(nodes_key), list) else []
    if isinstance(v, list):
        return v
    return []


def _field(obj: dict, gql: str, rest: str | None = None) -> Any:
    v = obj.get(gql)
    if v is not None:
        return v
    if rest:
        v = obj.get(rest)
    return v


def _extract_languages(projects: list[dict]) -> dict[str, float]:
    lang_count: dict[str, float] = {}
    for proj in projects:
        repo = proj.get("repository", {}) or {}
        nodes = _list_nodes(repo if isinstance(repo, dict) else {}, "languages")
        if nodes:
            for lang in nodes:
                if isinstance(lang, dict):
                    name = lang.get("name")
                    share = float(lang.get("share", 0) or 0)
                    if name:
                        lang_count[name] = lang_count.get(name, 0) + share
    return lang_count


def _count_recent_events(events: list[dict], days: int = 365) -> int:
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    count = 0
    for ev in events:
        created = _field(ev, "createdAt", "created_at")
        if created:
            try:
                ts = datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
                if ts >= cutoff:
                    count += 1
            except (ValueError, AttributeError):
                pass
    return count


def profile_to_signals(
    username: str,
    profile: dict[str, Any] | None,
    events: list[dict],
    projects: list[dict],
    mrs: list[dict],
    issues: list[dict],
) -> Signals | None:
    if not profile:
        return None

    log.info("profile_to_signals(%s): events=%d projects=%d mrs=%d issues=%d",
             username, len(events), len(projects), len(mrs), len(issues))

    languages = _extract_languages(projects)
    ranked = sorted(languages.items(), key=lambda x: -x[1])

    account_age_years = _years_since(_field(profile, "createdAt", "created_at"))

    recent_commits = _count_recent_events(events, 365)
    lifetime_commits = len([e for e in events if e.get("action") == "pushed"])

    total_mrs = len(mrs)
    merged_mrs = len([m for m in mrs if m.get("state") == "merged"])

    owned_paths = set()
    for p in projects:
        fp = _field(p, "fullPath", "path_with_namespace") or _field(p, "pathWithNamespace") or ""
        if fp:
            owned_paths.add(fp)

    commits_to_others = 0
    for ev in events:
        proj = ev.get("project", {}) or {}
        fp = _field(proj, "fullPath", "path_with_namespace") or ""
        if fp and fp not in owned_paths and ev.get("action") == "pushed":
            commits_to_others += 1

    reviews_given = 0
    review_comments = 0
    for mr in mrs:
        notes = _list_nodes(mr, "notes") if isinstance(mr.get("notes"), dict) else mr.get("notes") or []
        for note in notes:
            if isinstance(note, dict) and note.get("system") is False:
                review_comments += 1
        reviewers = _list_nodes(mr, "reviewers")
        if reviewers:
            reviews_given += 1

    unique_projects = set()
    for ev in events:
        proj = ev.get("project", {}) or {}
        fp = _field(proj, "fullPath", "path_with_namespace") or ""
        if fp:
            unique_projects.add(fp)
    for p in projects:
        fp = _field(p, "fullPath", "path_with_namespace") or _field(p, "pathWithNamespace") or ""
        if fp:
            unique_projects.add(fp)

    star_total = 0
    for p in projects:
        star_total += _safe_int(_field(p, "starCount", "star_count") or _field(p, "stargazerCount"))

    active_years = max(1, round(account_age_years))

    return Signals(
        username=username,
        name=_safe_str(_field(profile, "name")),
        avatar_url=_safe_str(_field(profile, "avatarUrl", "avatar_url")),
        bio=_safe_str(_field(profile, "bio")),
        location=_safe_str(_field(profile, "location")),
        followers=_safe_int(_field(profile, "followers", "followers") if isinstance(_field(profile, "followers"), int)
                            else _nested_count(profile, "followers", "totalCount")),
        public_projects=_safe_int(_field(profile, "public_projects") or _nested_count(profile, "projects", "totalCount")),
        account_age_years=account_age_years,
        languages=languages,
        ranked_languages=ranked,
        recent_commits=recent_commits,
        lifetime_commits=lifetime_commits,
        total_mrs=total_mrs,
        merged_mrs=merged_mrs,
        commits_to_others=commits_to_others,
        reviews_given=reviews_given,
        issues_created=len(issues),
        review_comments=review_comments,
        active_years=active_years,
        unique_projects_contributed=len(unique_projects),
        project_stars_total=star_total,
        owned_projects=len(projects),
    )
