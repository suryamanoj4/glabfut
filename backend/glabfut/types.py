from dataclasses import dataclass, field
from typing import Literal


StatKey = Literal["pac", "sho", "pas", "dri", "def", "phy"]
Position = Literal["ST", "RW", "CAM", "CM", "CDM", "CB"]
Family = Literal["Forward", "Playmaker", "Anchor"]
Finish = Literal["bronze", "silver", "gold", "totw", "toty", "icon"]
Tier = Literal["Bronze", "Silver", "Gold", "In-Form", "TOTY", "ICON"]


@dataclass
class Signals:
    username: str
    name: str | None
    avatar_url: str | None
    bio: str | None
    location: str | None
    followers: int
    public_projects: int
    account_age_years: float
    languages: dict[str, float]
    ranked_languages: list[tuple[str, float]]
    recent_commits: int
    lifetime_commits: int
    total_mrs: int
    merged_mrs: int
    commits_to_others: int
    reviews_given: int
    issues_created: int
    review_comments: int
    active_years: int
    unique_projects_contributed: int
    project_stars_total: int
    owned_projects: int


@dataclass
class Stats:
    pac: float
    sho: float
    pas: float
    dri: float
    def_: float
    phy: float


@dataclass
class Metrics:
    recent_commits: int
    merged_mrs: int
    followers: int
    review_comments: int
    languages: int
    issues: int
    reviews: int
    lifetime_commits: int
    active_years: int
    projects: int


@dataclass
class Card:
    username: str
    name: str | None
    avatar_url: str | None
    bio: str | None
    location: str | None
    stats: Stats
    overall: int
    base_ovr: int
    position: Position
    family: Family
    finish: Finish
    tier: Tier
    archetype: str
    archetype_blurb: str
    top_language: str | None
    metrics: Metrics
    playstyles: list[str]
