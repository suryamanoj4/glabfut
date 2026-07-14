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


def _mr_role(mr: dict[str, Any]) -> str:
    return str(mr.get("role") or "").lower()


def _issue_role(issue: dict[str, Any]) -> str:
    return str(issue.get("role") or "").lower()


def _is_reviewer_for_user(mr: dict[str, Any], username: str, profile: dict[str, Any] | None) -> bool:
    reviewers = _list_nodes(mr, "reviewers")
    targets = {
        username.lower(),
        str((profile or {}).get("username") or "").lower(),
        str((profile or {}).get("name") or "").lower(),
    }
    targets.discard("")
    for reviewer in reviewers:
        if not isinstance(reviewer, dict):
            continue
        values = {
            str(reviewer.get("username") or "").lower(),
            str(reviewer.get("name") or "").lower(),
        }
        if targets & values:
            return True
    return False


def _note_authored_by_user(note: dict[str, Any], username: str, profile: dict[str, Any] | None) -> bool:
    author = note.get("author") if isinstance(note, dict) else None
    if not isinstance(author, dict):
        return False
    targets = {
        username.lower(),
        str((profile or {}).get("username") or "").lower(),
        str((profile or {}).get("name") or "").lower(),
        str((profile or {}).get("publicEmail") or "").lower(),
        str((profile or {}).get("email") or "").lower(),
    }
    targets.discard("")
    values = {
        str(author.get("username") or "").lower(),
        str(author.get("name") or "").lower(),
        str(author.get("email") or "").lower(),
    }
    values.discard("")
    return bool(targets & values)


def profile_to_signals(
    username: str,
    profile: dict[str, Any] | None,
    events: list[dict],
    projects: list[dict],
    mrs: list[dict],
    issues: list[dict],
    commit_stats: dict[str, int] | None = None,
) -> Signals | None:
    if not profile:
        return None

    log.info("profile_to_signals(%s): events=%d projects=%d mrs=%d issues=%d",
             username, len(events), len(projects), len(mrs), len(issues))

    languages = _extract_languages(projects)
    ranked = sorted(languages.items(), key=lambda x: -x[1])

    account_age_years = _years_since(_field(profile, "createdAt", "created_at"))

    commit_stats = commit_stats or {}
    recent_commits = _safe_int(commit_stats.get("recent_commits"))
    lifetime_commits = _safe_int(commit_stats.get("lifetime_commits")) or recent_commits

    total_mrs = len(mrs)
    merged_mrs = len([m for m in mrs if m.get("state") == "merged"])

    commits_to_others = _safe_int(commit_stats.get("commits_to_others"))

    review_mrs = [mr for mr in mrs if _is_reviewer_for_user(mr, username, profile)]
    if not review_mrs:
        review_mrs = [mr for mr in mrs if "assigned" in _mr_role(mr) and "authored" not in _mr_role(mr)]
    reviews_given = len(review_mrs)
    review_comments = 0
    for mr in review_mrs:
        notes = _list_nodes(mr, "notes") if isinstance(mr.get("notes"), dict) else mr.get("notes") or []
        if notes:
            for note in notes:
                if isinstance(note, dict) and note.get("system") is False and _note_authored_by_user(note, username, profile):
                    review_comments += 1

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
        issues_created=len([issue for issue in issues if "authored" in _issue_role(issue)]),
        review_comments=review_comments,
        active_years=active_years,
        unique_projects_contributed=len(unique_projects),
        project_stars_total=star_total,
        owned_projects=len(projects),
    )
