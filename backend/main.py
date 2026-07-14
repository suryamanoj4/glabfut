from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from contextlib import asynccontextmanager
from itertools import combinations
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("glabfut")

from glabfut.cache import TTLCache
from glabfut.client import GitLabProfileClient
from glabfut.constants import POSITION_FAMILY, POSITION_PROFILES, STAT_KEYS
from glabfut.engine import build_card
from glabfut.signals import profile_to_signals

cache = TTLCache(capacity=256, ttl=7200)


class LineupRequest(BaseModel):
    players: list[str] = Field(min_length=11, max_length=11)
    gitlab_url: str | None = None


def _cache_key(username: str, gitlab_url: str, token: str) -> str:
    token_scope = hashlib.sha256(token.encode("utf-8")).hexdigest()[:12] if token else "anon"
    return f"card:{gitlab_url}:{token_scope}:{username.lower()}"


def _resolve_creds(gitlab_url: str | None, token: str | None) -> tuple[str, str]:
    url = (gitlab_url or "https://gitlab.com").strip()
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    tok = (token or "").strip()
    return url, tok


def _project_paths(projects: list[dict[str, Any]]) -> set[str]:
    paths: set[str] = set()
    for project in projects:
        full_path = (
            project.get("fullPath")
            or project.get("pathWithNamespace")
            or project.get("path_with_namespace")
        )
        if full_path:
            paths.add(str(full_path))
    return paths


def _card_payload(card, gitlab_url: str) -> dict[str, Any]:
    metrics = {
        "recent_commits": card.metrics.recent_commits,
        "merged_mrs": card.metrics.merged_mrs,
        "followers": card.metrics.followers,
        "review_comments": card.metrics.review_comments,
        "languages": card.metrics.languages,
        "issues": card.metrics.issues,
        "reviews": card.metrics.reviews,
        "lifetime_commits": card.metrics.lifetime_commits,
        "active_years": card.metrics.active_years,
        "projects": card.metrics.projects,
    }
    return {
        "scouted_from": gitlab_url,
        "username": card.username,
        "name": card.name,
        "avatar_url": card.avatar_url,
        "bio": card.bio,
        "location": card.location,
        "stats": {
            "pac": card.stats.pac,
            "sho": card.stats.sho,
            "pas": card.stats.pas,
            "dri": card.stats.dri,
            "def": card.stats.def_,
            "phy": card.stats.phy,
        },
        "overall": card.overall,
        "base_ovr": card.base_ovr,
        "position": card.position,
        "family": card.family,
        "tier": card.tier,
        "finish": card.finish,
        "archetype": card.archetype,
        "archetype_blurb": card.archetype_blurb,
        "top_language": card.top_language,
        "metrics": metrics,
        "playstyles": card.playstyles,
    }


def _player_slot_score(player: dict[str, Any], slot: str) -> tuple[float, str]:
    position = player["card"]["position"]
    stats = player["card"]["stats"]
    total = sum(float(stats[key]) for key in STAT_KEYS) or 1.0
    normalized = {key: float(stats[key]) / total for key in STAT_KEYS}
    profile = POSITION_PROFILES[slot]
    similarity = sum(normalized[key] * profile[key] for key in STAT_KEYS)
    exact_bonus = 1.0 if position == slot else 0.0
    family_bonus = 0.15 if POSITION_FAMILY.get(position) == POSITION_FAMILY.get(slot) else 0.0
    return similarity + exact_bonus + family_bonus, player["username"]


def _duel_result(player1: dict[str, Any], player2: dict[str, Any]) -> dict[str, Any]:
    stat_keys = ["pac", "sho", "pas", "dri", "def", "phy"]
    winners: dict[str, str] = {}
    p1_score = 0
    p2_score = 0
    for key in stat_keys:
        left = player1["stats"][key]
        right = player2["stats"][key]
        if left > right:
            winners[key] = "player1"
            p1_score += 1
        elif right > left:
            winners[key] = "player2"
            p2_score += 1
        else:
            winners[key] = "draw"

    if player1["overall"] > player2["overall"]:
        overall_winner = "player1"
        p1_score += 1
    elif player2["overall"] > player1["overall"]:
        overall_winner = "player2"
        p2_score += 1
    else:
        overall_winner = "draw"

    if p1_score > p2_score:
        verdict = "player1"
    elif p2_score > p1_score:
        verdict = "player2"
    else:
        verdict = "draw"

    return {
        "player1": player1,
        "player2": player2,
        "stat_winners": winners,
        "overall_winner": overall_winner,
        "verdict": verdict,
        "score": f"{p1_score}-{p2_score}",
    }


def _assign_lineup(players: list[dict[str, Any]]) -> list[dict[str, Any]]:
    slots = ["ST", "ST", "RW", "RW", "CAM", "CAM", "CM", "CM", "CDM", "CB", "CB"]
    remaining = list(players)
    assigned: list[dict[str, Any]] = []
    for slot in slots:
        exact_idx = max(
            range(len(remaining)),
            key=lambda idx: _player_slot_score(remaining[idx], slot),
        )
        player = remaining.pop(exact_idx)
        assigned.append({**player, "slot": slot})
    return assigned


def _build_lineup_response(players: list[dict[str, Any]]) -> dict[str, Any]:
    assigned = _assign_lineup(players)
    links: list[dict[str, Any]] = []
    chemistry_counts = {player["username"]: 0 for player in assigned}
    for left, right in combinations(assigned, 2):
        overlap = sorted(set(left["project_paths"]) & set(right["project_paths"]))
        if overlap:
            chemistry_counts[left["username"]] += 1
            chemistry_counts[right["username"]] += 1
        links.append(
            {
                "player1": left["username"],
                "player2": right["username"],
                "shared_projects": overlap,
                "strength": len(overlap),
            }
        )

    connected_pairs = len([link for link in links if link["strength"] > 0])
    max_pairs = len(links) or 1
    team_chemistry = round(100 * connected_pairs / max_pairs)

    lineup = []
    for player in assigned:
        chemistry = chemistry_counts[player["username"]]
        boost = min(5, chemistry)
        boosted_overall = min(99, player["card"]["overall"] + boost)
        lineup.append(
            {
                "slot": player["slot"],
                "username": player["username"],
                "chemistry_links": chemistry,
                "chemistry_boost": boost,
                "boosted_overall": boosted_overall,
                "shared_projects": player["project_paths"],
                "card": player["card"],
            }
        )

    return {
        "formation": "Adaptive XI",
        "team_chemistry": team_chemistry,
        "players": lineup,
        "links": links,
    }


async def _build_user_bundle(username: str, gitlab_url: str, token: str) -> dict[str, Any]:
    ck = _cache_key(username, gitlab_url, token)
    cached = cache.get(ck)
    if cached and "error" not in cached:
        response = dict(cached)
        project_paths = response.pop("_project_paths", [])
        return {"response": response, "projects": project_paths}

    client = GitLabProfileClient(gitlab_url=gitlab_url, token=token)
    try:
        profile = await client.fetch_user_profile(username)
        if not profile:
            err = {"error": "User not found", "status": 404}
            cache.set(ck, err)
            raise HTTPException(status_code=404, detail="User not found")

        events, projects, mrs, issues = await _fetch_user_data(client, username, profile)
        signals = profile_to_signals(
            username=username,
            profile=profile,
            events=events,
            projects=projects,
            mrs=mrs,
            issues=issues,
            commit_stats=await client.fetch_user_commit_stats(username, profile, projects, events),
        )
        if not signals:
            raise HTTPException(status_code=404, detail="Could not process user data")

        card = build_card(signals)
        response = _card_payload(card, gitlab_url)
        project_paths = sorted(_project_paths(projects))
        cache.set(ck, {**response, "_project_paths": project_paths})
        return {"response": response, "projects": project_paths}
    finally:
        await client.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(
    title="glabfut",
    description="Your GitLab profile, rated out of 99",
    version="0.1.0",
    lifespan=lifespan,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
static_dir = os.path.join(BASE_DIR, "static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/card/{username}")
async def get_card(
    request: Request,
    username: str,
    gitlab_url: str = Query(default=None),
    x_gitlab_url: str | None = Header(default=None),
    x_gitlab_token: str | None = Header(default=None),
):
    url, token = _resolve_creds(x_gitlab_url or gitlab_url, x_gitlab_token)
    if not token:
        raise HTTPException(
            status_code=401,
            detail="GitLab token required. Set GITLAB_TOKEN env var or pass X-GitLab-Token header.",
        )

    cached = cache.get(_cache_key(username, url, token))
    if cached:
        if "error" in cached:
            raise HTTPException(status_code=cached["status"], detail=cached["error"])
        payload = dict(cached)
        payload.pop("_project_paths", None)
        return JSONResponse(payload)

    bundle = await _build_user_bundle(username, url, token)
    return JSONResponse(bundle["response"])


@app.get("/card/{username}/page", response_class=HTMLResponse)
async def card_page(
    request: Request,
    username: str,
    gitlab_url: str = Query(default=None),
):
    return templates.TemplateResponse(
        request,
        "card.html",
        {"username": username, "gitlab_url": gitlab_url or ""},
    )


@app.get("/duel/{username1}/{username2}")
async def duel(
    request: Request,
    username1: str,
    username2: str,
    gitlab_url: str = Query(default=None),
    x_gitlab_url: str | None = Header(default=None),
    x_gitlab_token: str | None = Header(default=None),
):
    url, token = _resolve_creds(x_gitlab_url or gitlab_url, x_gitlab_token)
    if not token:
        raise HTTPException(status_code=401, detail="GitLab token required.")
    left, right = await _fetch_bundles([username1, username2], url, token)
    return JSONResponse(_duel_result(left["response"], right["response"]))


@app.get("/duel/{username1}/{username2}/page", response_class=HTMLResponse)
async def duel_page(request: Request, username1: str, username2: str):
    return templates.TemplateResponse(
        request,
        "duel.html",
        {"username1": username1, "username2": username2},
    )


@app.post("/team/lineup")
async def team_lineup(
    payload: LineupRequest,
    request: Request,
    x_gitlab_url: str | None = Header(default=None),
    x_gitlab_token: str | None = Header(default=None),
):
    url, token = _resolve_creds(x_gitlab_url or payload.gitlab_url, x_gitlab_token)
    if not token:
        raise HTTPException(status_code=401, detail="GitLab token required.")
    normalized = [player.strip() for player in payload.players if player.strip()]
    if len(normalized) != 11 or len(set(name.lower() for name in normalized)) != 11:
        raise HTTPException(status_code=422, detail="Lineup must contain 11 unique usernames.")

    bundles = await _fetch_bundles(normalized, url, token)
    players = [
        {
            "username": username,
            "card": bundle["response"],
            "project_paths": bundle["projects"],
        }
        for username, bundle in zip(normalized, bundles)
    ]
    return JSONResponse(_build_lineup_response(players))


@app.get("/team/lineup/page", response_class=HTMLResponse)
async def lineup_page(request: Request):
    return templates.TemplateResponse(request, "lineup.html", {})


async def _fetch_bundles(usernames: list[str], gitlab_url: str, token: str) -> list[dict[str, Any]]:
    results = await asyncio.gather(
        *[_build_user_bundle(username, gitlab_url, token) for username in usernames],
        return_exceptions=True,
    )
    bundles: list[dict[str, Any]] = []
    for result in results:
        if isinstance(result, HTTPException):
            raise result
        if isinstance(result, Exception):
            raise HTTPException(status_code=502, detail=str(result))
        bundles.append(result)
    return bundles


async def _fetch_user_data(client: GitLabProfileClient, username: str, profile: dict[str, Any]):
    user_id = profile.get("id") if isinstance(profile, dict) else None
    events_task = client.fetch_user_events(username, user_id=user_id)
    projects_task = client.fetch_user_projects(username)
    mrs_task = client.fetch_user_mrs(username)
    issues_task = client.fetch_user_issues(username, user_id=user_id)

    results = await asyncio.gather(events_task, projects_task, mrs_task, issues_task, return_exceptions=True)
    events = results[0] if not isinstance(results[0], Exception) else []
    projects = results[1] if not isinstance(results[1], Exception) else []
    mrs = results[2] if not isinstance(results[2], Exception) else []
    issues = results[3] if not isinstance(results[3], Exception) else []
    log.info(
        "Fetched user data for %s: events=%d projects=%d mrs=%d issues=%d profile=%s",
        username,
        len(events),
        len(projects),
        len(mrs),
        len(issues),
        bool(profile),
    )
    return events, projects, mrs, issues
