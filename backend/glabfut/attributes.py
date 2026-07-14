from .types import Signals
from .constants import ELITE_REFS, score99


def compute_metrics(signals: Signals) -> dict[str, int]:
    return {
        "recent_commits": signals.recent_commits,
        "merged_mrs": signals.merged_mrs,
        "followers": signals.followers,
        "review_comments": signals.review_comments,
        "languages": len(signals.languages),
        "issues": signals.issues_created,
        "reviews": signals.reviews_given,
        "lifetime_commits": signals.lifetime_commits,
        "active_years": signals.active_years,
        "projects": signals.unique_projects_contributed,
    }


def metrics_to_scores(metrics: dict[str, int]) -> dict[str, int]:
    return {k: score99(v, ELITE_REFS[k]) for k, v in metrics.items()}


PLAYSTYLE_RULES: list[tuple[str, str, object]] = [
    ("Workhorse", "recent_commits", lambda v: v >= 120),
    ("Veteran", "active_years", lambda v: v >= 5),
    ("Connector", "merged_mrs", lambda v: v >= 30),
    ("Maintainer", "reviews", lambda v: v >= 30),
    ("Prolific", "projects", lambda v: v >= 20),
    ("Polyglot", "languages", lambda v: v >= 6),
    ("Magnetic", "followers", lambda v: v >= 200),
    ("Marathoner", "lifetime_commits", lambda v: v >= 3000),
    ("Sharp Shooter", "merged_mrs", lambda v: v >= 100),
    ("Globetrotter", "projects", lambda v: v >= 50),
]


def derive_playstyles(metrics: dict[str, int]) -> list[str]:
    return [name for name, key, rule in PLAYSTYLE_RULES if rule(metrics.get(key, 0))]


ARCHETYPE_BLURBS: dict[str, str] = {
    "Poacher": "Lives for goals. Clinical in the box, thrives on service.",
    "Winger": "Speed merchant who beats defenders wide and delivers.",
    "Target Man": "Physical presence up front, holds up play and brings others in.",
    "Fantasista": "Pure artistry — flair, vision, and impossible passes.",
    "Playmaker": "The midfield metronome. Dictates tempo, controls the game.",
    "Mezzala": "Box-to-box engine who contributes at both ends.",
    "Regista": "Deep-lying quarterback who sprays passes from the back.",
    "Libero": "Ball-playing defender who reads the game and builds from deep.",
    "Guardian": "Stopper. A wall in defense who wins every duel.",
    "Galactico": "Generational talent. Elite across every dimension.",
    "Founder": "Laid the foundations. The game runs on your code.",
}
