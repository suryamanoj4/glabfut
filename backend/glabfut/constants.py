import math
from .types import Family, Position


STAT_LABELS: dict[str, str] = {
    "pac": "PAC",
    "sho": "SHO",
    "pas": "PAS",
    "dri": "DRI",
    "def": "DEF",
    "phy": "PHY",
}

STAT_NAMES: dict[str, str] = {
    "pac": "Pace",
    "sho": "Shooting",
    "pas": "Passing",
    "dri": "Dribbling",
    "def": "Defending",
    "phy": "Physical",
}

POSITION_ORDER: list[Position] = ["ST", "RW", "CAM", "CM", "CDM", "CB"]

FAMILY_POSITIONS: dict[Family, list[Position]] = {
    "Forward": ["ST", "RW"],
    "Playmaker": ["CAM", "CM"],
    "Anchor": ["CDM", "CB"],
}

POSITION_FAMILY: dict[Position, Family] = {
    "ST": "Forward", "RW": "Forward",
    "CAM": "Playmaker", "CM": "Playmaker",
    "CDM": "Anchor", "CB": "Anchor",
}

ARCHETYPES: dict[str, dict] = {
    "Poacher": {
        "blurb": "Lives for goals. Clinical in the box, thrives on service.",
        "family": "Forward", "stats": ["sho", "pac"],
    },
    "Winger": {
        "blurb": "Speed merchant who beats defenders wide and delivers.",
        "family": "Forward", "stats": ["pac", "dri"],
    },
    "Target Man": {
        "blurb": "Physical presence up front, holds up play and brings others in.",
        "family": "Forward", "stats": ["phy", "sho"],
    },
    "Fantasista": {
        "blurb": "Pure artistry — flair, vision, and impossible passes.",
        "family": "Playmaker", "stats": ["dri", "pas"],
    },
    "Playmaker": {
        "blurb": "The midfield metronome. Dictates tempo, controls the game.",
        "family": "Playmaker", "stats": ["pas"],
    },
    "Mezzala": {
        "blurb": "Box-to-box engine who contributes at both ends.",
        "family": "Playmaker", "stats": ["pac", "phy", "sho"],
    },
    "Regista": {
        "blurb": "Deep-lying quarterback who sprays passes from the back.",
        "family": "Anchor", "stats": ["pas", "def"],
    },
    "Libero": {
        "blurb": "Ball-playing defender who reads the game and builds from deep.",
        "family": "Anchor", "stats": ["def", "pas"],
    },
    "Guardian": {
        "blurb": "Stopper. A wall in defense who wins every duel.",
        "family": "Anchor", "stats": ["def", "phy"],
    },
    "Galactico": {
        "blurb": "Generational talent. Elite across every dimension.",
        "family": "Forward", "stats": ["pac", "sho", "pas", "dri", "def", "phy"],
    },
    "Founder": {
        "blurb": "Laid the foundations. The game runs on your code.",
        "family": "Playmaker", "stats": ["pas", "def"],
    },
}

FINISH_THRESHOLDS = {
    "icon": 90,
    "toty": 85,
    "gold": 75,
    "silver": 65,
    "bronze": 0,
}

CARD_TIERS: dict[str, str] = {
    "bronze": "Bronze",
    "silver": "Silver",
    "gold": "Gold",
    "totw": "In-Form",
    "toty": "TOTY",
    "icon": "ICON",
}

TIER_COLORS: dict[str, str] = {
    "bronze": "#8B6914",
    "silver": "#9BA4B5",
    "gold": "#E6B422",
    "totw": "#E03E52",
    "toty": "#3B7AFF",
    "icon": "#F3D688",
}

TIER_BG: dict[str, str] = {
    "bronze": "linear-gradient(135deg, #2A1A0C, #5C3A1A)",
    "silver": "linear-gradient(135deg, #2B3135, #5A6670)",
    "gold": "linear-gradient(135deg, #3A2806, #8B6914)",
    "totw": "linear-gradient(135deg, #4A0A14, #E03E52)",
    "toty": "linear-gradient(135deg, #10254F, #3B7AFF)",
    "icon": "linear-gradient(135deg, #2A1A45, #F3D688)",
}

# Weights for OVR calculation per family
FAMILY_WEIGHTS: dict[str, dict[str, float]] = {
    "Forward": {"pac": 0.25, "sho": 0.30, "pas": 0.10, "dri": 0.20, "def": 0.05, "phy": 0.10},
    "Playmaker": {"pac": 0.10, "sho": 0.15, "pas": 0.30, "dri": 0.20, "def": 0.10, "phy": 0.15},
    "Anchor": {"pac": 0.05, "sho": 0.10, "pas": 0.20, "dri": 0.05, "def": 0.35, "phy": 0.25},
}

# Elite reference values for metric scoring (0-99)
ELITE_REFS: dict[str, float] = {
    "recent_commits": 3000,
    "merged_mrs": 2000,
    "followers": 50000,
    "review_comments": 2000,
    "languages": 15,
    "issues": 1500,
    "reviews": 2000,
    "lifetime_commits": 50000,
    "active_years": 15,
    "projects": 100,
}

# Sigmoid legacy constants
LEGACY_MIDPOINT = 8.0
LEGACY_STEEPNESS = 0.6
LEGACY_FACTORS = ["active_years", "followers_log", "mrs_log", "account_age"]

# Profile to position shape mapping
POSITION_PROFILES: dict[Position, dict[str, float]] = {
    "ST": {"pac": 1.0, "sho": 1.0, "pas": 0.4, "dri": 0.7, "def": 0.2, "phy": 0.8},
    "RW": {"pac": 1.0, "sho": 0.8, "pas": 0.7, "dri": 1.0, "def": 0.2, "phy": 0.5},
    "CAM": {"pac": 0.7, "sho": 0.8, "pas": 1.0, "dri": 1.0, "def": 0.3, "phy": 0.5},
    "CM": {"pac": 0.7, "sho": 0.6, "pas": 1.0, "dri": 0.7, "def": 0.7, "phy": 0.7},
    "CDM": {"pac": 0.5, "sho": 0.4, "pas": 0.7, "dri": 0.4, "def": 1.0, "phy": 0.9},
    "CB": {"pac": 0.3, "sho": 0.3, "pas": 0.5, "dri": 0.3, "def": 1.0, "phy": 1.0},
}

STAT_KEYS = ["pac", "sho", "pas", "dri", "def", "phy"]

# Map stat keys to Stats dataclass attribute names
STAT_ATTRS: dict[str, str] = {
    "pac": "pac", "sho": "sho", "pas": "pas",
    "dri": "dri", "def": "def_", "phy": "phy",
}


def score99(value: int | float, ref: float) -> int:
    if value <= 0:
        return 1
    s = 99 * math.log10(value + 1) / math.log10(ref + 1)
    return max(1, min(99, round(s)))
