from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Header, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("glabfut")

from glabfut.client import GitLabProfileClient
from glabfut.cache import TTLCache
from glabfut.engine import build_card
from glabfut.signals import profile_to_signals

cache = TTLCache(capacity=256, ttl=7200)


def _cache_key(username: str, gitlab_url: str) -> str:
    return f"card:{gitlab_url}:{username.lower()}"


def _resolve_creds(
    gitlab_url: str | None,
    token: str | None,
) -> tuple[str, str]:
    url = gitlab_url or os.getenv("GITLAB_URL", "https://gitlab.com")
    tok = token or os.getenv("GITLAB_TOKEN", "")
    return url, tok


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
    url, token = _resolve_creds(
        x_gitlab_url or gitlab_url,
        x_gitlab_token,
    )

    ck = _cache_key(username, url)
    cached = cache.get(ck)
    if cached:
        if "error" in cached:
            raise HTTPException(status_code=cached["status"], detail=cached["error"])
        return JSONResponse(cached)

    log.info("Fetching card for user=%s gitlab_url=%s token_provided=%s",
             username, url, bool(token))

    if not token:
        raise HTTPException(
            status_code=401,
            detail="GitLab token required. Set GITLAB_TOKEN env var or pass X-GitLab-Token header.",
        )

    client = GitLabProfileClient(gitlab_url=url, token=token)
    profile = await client.fetch_user_profile(username)
    if not profile:
        err = {"error": "User not found", "status": 404}
        cache.set(ck, err)
        raise HTTPException(status_code=404, detail="User not found")

    events, projects, mrs, issues = await _fetch_user_data(client, username)

    signals = profile_to_signals(
        username=username,
        profile=profile,
        events=events,
        projects=projects,
        mrs=mrs,
        issues=issues,
    )
    if not signals:
        raise HTTPException(status_code=404, detail="Could not process user data")

    card = build_card(signals)

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

    response = {
        "scouted_from": url,
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

    cache.set(ck, response)
    await client.close()
    return JSONResponse(response)


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


async def _fetch_user_data(client, username: str):
    from asyncio import gather

    events_task = client.fetch_user_events(username)
    projects_task = client.fetch_user_projects(username)
    mrs_task = client.fetch_user_mrs(username)
    issues_task = client.fetch_user_issues(username)

    results = await gather(
        events_task, projects_task, mrs_task, issues_task,
        return_exceptions=True,
    )

    events = results[0] if not isinstance(results[0], Exception) else []
    projects = results[1] if not isinstance(results[1], Exception) else []
    mrs = results[2] if not isinstance(results[2], Exception) else []
    issues = results[3] if not isinstance(results[3], Exception) else []

    return events, projects, mrs, issues
