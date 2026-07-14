# glabfut ⚽

Your **GitLab** profile, turned into a World-Cup-style player card — rated out of 99.

A GitLab-native port of [github.com/younesfdj/gitfut](https://github.com/younesfdj/gitfut), built with **FastAPI** + **[glabflow](https://gitlab.com/ranjithraj/glabflow)**.

## How it works

Six signals from a GitLab profile, each mapped to a FUT stat:

| Stat | Scouts from |
|------|-------------|
| **PAC** (Pace) | Commits in the last year |
| **SHO** (Shooting) | Merged MRs + commits to external projects |
| **PAS** (Passing) | Review comments + followers |
| **DRI** (Dribbling) | Language diversity × projects contributed |
| **DEF** (Defending) | Reviews given + issues |
| **PHY** (Physical) | Lifetime commits + account age |

Your **overall** is headline. Raw stats cap at 88 — the 90s are a legacy gate, earned with years and influence.

## Setup

### 1. Install

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/).

```bash
cd backend
uv sync
```

### 2. Environment variables

```bash
export GITLAB_TOKEN="glpat-your-token"     # Required for gitlab.com
export GITLAB_URL="https://gitlab.com"      # Default, change for self-hosted
```

For self-hosted GitLab:
```bash
export GITLAB_URL="https://gitlab.yourcompany.com"
export GITLAB_TOKEN="glpat-xxx"
```

### 3. Run

```bash
cd backend
uv run uvicorn main:app --reload --port 8000
```

Open http://localhost:8000

## Usage

- **`/`** — Home page with search
- **`/card/{username}`** — Card JSON API
- **`/card/{username}/page`** — Card HTML page (with export to PNG)

Supports `?gitlab_url=` query param for any GitLab instance:

```
http://localhost:8000/card/torvalds/page
http://localhost:8000/card/johndoe/page?gitlab_url=https://gitlab.example.com
```

## Tech

- **Python 3.10+** with **FastAPI**
- **[glabflow](https://gitlab.com/ranjithraj/glabflow)** — GraphQL-first GitLab client
- **Jinja2** templates, vanilla CSS/JS frontend
- **html-to-image** — client-side PNG export
- In-memory TTL cache
