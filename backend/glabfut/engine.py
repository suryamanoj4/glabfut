from __future__ import annotations

import math

from .attributes import (
    ARCHETYPE_BLURBS,
    compute_metrics,
    derive_playstyles,
)
from .constants import (
    CARD_TIERS,
    FAMILY_WEIGHTS,
    POSITION_FAMILY,
    POSITION_PROFILES,
    STAT_ATTRS,
    STAT_KEYS,
)
from .types import (
    Card,
    Family,
    Finish,
    Metrics,
    Signals,
    Stats,
)


def _log10p1(x: int | float) -> float:
    return math.log10(max(0, x) + 1)


def _sqrt(x: int | float) -> float:
    return math.sqrt(max(0, x))


def raw_stats(signals: Signals) -> Stats:
    return Stats(
        pac=36 + 12 * _log10p1(signals.recent_commits),
        sho=36 + 13 * _log10p1(signals.merged_mrs) + 5 * _log10p1(signals.commits_to_others),
        pas=40 + 12 * _log10p1(signals.review_comments) + 9 * _log10p1(signals.followers),
        dri=58 + 7 * _sqrt(len(signals.languages)) + 2 * _sqrt(signals.unique_projects_contributed),
        def_=40 + 14 * _log10p1(signals.reviews_given + signals.issues_created),
        phy=40 + 9 * _log10p1(signals.lifetime_commits) + 2.2 * min(signals.active_years, 12),
    )


def _clamp(x: float, lo: float = 1, hi: float = 99) -> float:
    return max(lo, min(hi, x))


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 1
    m = _mean(values)
    v = sum((x - m) ** 2 for x in values) / (len(values) - 1)
    return math.sqrt(v)


def _sigmoid(x: float, midpoint: float = 8.0, steepness: float = 0.6) -> float:
    return 1 / (1 + math.exp(-steepness * (x - midpoint)))


def center_score(signals: Signals) -> float:
    mag = (
        0.3 * _log10p1(signals.lifetime_commits) / 4
        + 0.25 * _log10p1(signals.followers) / 5
        + 0.25 * _log10p1(signals.merged_mrs) / 3
        + 0.1 * _log10p1(signals.account_age_years * 365) / 3
        + 0.1 * len(signals.languages) / 15
    )
    return 50 + 30 * _sigmoid(mag - 0.5, midpoint=0, steepness=3)


def zscore(stats: Stats) -> dict[str, float]:
    values = [stats.pac, stats.sho, stats.pas, stats.dri, stats.def_, stats.phy]
    m = _mean(values)
    s = _std(values)
    if s == 0:
        s = 1
    return {k: (v - m) / s for k, v in zip(STAT_KEYS, values)}


def spike(profile: dict[str, float], center: float) -> dict[str, float]:
    out = dict(profile)
    attack = ["pac", "sho", "pas", "dri"]
    attack_mean = _mean([out[k] for k in attack])
    for k in attack:
        out[k] = out[k] * 0.6 + attack_mean * 0.4
    return out


def apply_tension(profile: dict[str, float]) -> dict[str, float]:
    out = dict(profile)
    antagonist_pairs = [("sho", "def"), ("dri", "phy"), ("pac", "def")]
    for a, b in antagonist_pairs:
        avg = (out[a] + out[b]) / 2
        if out[a] > 70 and out[b] > 70:
            penalty = (out[a] + out[b] - 140) * 0.15
            out[a] -= penalty * 0.5
            out[b] -= penalty * 0.5
    return out


def position_from_shape(stats: dict[str, float]) -> tuple[Position, Family]:
    total = sum(stats.values())
    if total == 0:
        return "CM", "Playmaker"
    norm = {k: v / total for k, v in stats.items()}

    best_pos: Position = "CM"
    best_sim = -1.0

    for pos, profile in POSITION_PROFILES.items():
        sim = sum(norm[k] * profile[k] for k in STAT_KEYS)
        if sim > best_sim:
            best_sim = sim
            best_pos = pos

    return best_pos, POSITION_FAMILY[best_pos]


def weighted_ovr(stats: Stats, family: Family) -> int:
    weights = FAMILY_WEIGHTS[family]
    weighted = sum(
        getattr(stats, STAT_ATTRS[k]) * weights[k]
        for k in STAT_KEYS
    )
    return min(99, round(weighted))


def legacy_score(signals: Signals) -> float:
    factors = {
        "active_years": min(signals.active_years / 10, 1),
        "followers_log": min(_log10p1(signals.followers) / 5, 1),
        "mrs_log": min(_log10p1(signals.merged_mrs) / 3, 1),
        "account_age": min(signals.account_age_years / 10, 1),
    }
    composite = sum(factors.values()) / len(factors)
    return _sigmoid(composite * 10, midpoint=5, steepness=1.0)


def pick_finish(overall: int, legacy: float, has_spike: bool, username: str) -> Finish:
    icon_names = {"torvalds", "linus", "gitlab", "yang", "github"}
    if overall >= 90 or username.lower() in icon_names:
        return "icon"
    if overall >= 85 and legacy >= 0.5:
        return "toty"
    if has_spike and overall >= 65:
        return "totw"
    if overall >= 75:
        return "gold"
    if overall >= 65:
        return "silver"
    return "bronze"


def detect_spike(signals: Signals) -> bool:
    return signals.recent_commits > max(200, signals.lifetime_commits * 0.3)


def archetype_from_shape(stats: dict[str, float], finish: Finish) -> str:
    if finish == "icon":
        return "Galactico" if stats["phy"] > 85 else "Fantasista"
    if finish == "toty":
        return "Libero" if stats["def"] > stats["sho"] else "Galactico"

    ordered = sorted(STAT_KEYS, key=lambda k: -stats[k])
    top = ordered[0]

    scores = {
        "Poacher": stats["sho"],
        "Winger": stats["pac"],
        "Target Man": stats["phy"],
        "Fantasista": stats["dri"],
        "Playmaker": stats["pas"],
        "Mezzala": (stats["pac"] + stats["phy"] + stats["sho"]) / 3,
        "Regista": (stats["pas"] + stats["def"]) / 2,
        "Libero": (stats["def"] + stats["pas"]) / 2,
        "Guardian": (stats["def"] + stats["phy"]) / 2,
    }

    return max(scores, key=scores.get)


def build_card(signals: Signals) -> Card:
    raw = raw_stats(signals)

    center = center_score(signals)

    raw_dict: dict[str, float] = {
        "pac": raw.pac,
        "sho": raw.sho,
        "pas": raw.pas,
        "dri": raw.dri,
        "def": raw.def_,
        "phy": raw.phy,
    }

    tensioned = apply_tension(raw_dict)
    spiked = spike(tensioned, center)
    spiked = {k: _clamp(v) for k, v in spiked.items()}

    final_stats = Stats(
        pac=round(spiked["pac"]),
        sho=round(spiked["sho"]),
        pas=round(spiked["pas"]),
        dri=round(spiked["dri"]),
        def_=round(spiked["def"]),
        phy=round(spiked["phy"]),
    )

    pos, family = position_from_shape(spiked)

    base_ovr = weighted_ovr(final_stats, family)
    overall = base_ovr

    legacy = legacy_score(signals)
    overall = min(88, base_ovr)
    if legacy > 0.5:
        overall = min(99, base_ovr + round(10 * (legacy - 0.5) * 2))

    has_spike = detect_spike(signals)
    finish = pick_finish(overall, legacy, has_spike, signals.username)

    arch = archetype_from_shape(spiked, finish)
    blurb = ARCHETYPE_BLURBS.get(arch, "")

    metrics_data = compute_metrics(signals)
    playstyles = derive_playstyles(metrics_data)

    top_lang = signals.ranked_languages[0][0] if signals.ranked_languages else None

    metrics_obj = Metrics(
        recent_commits=metrics_data["recent_commits"],
        merged_mrs=metrics_data["merged_mrs"],
        followers=metrics_data["followers"],
        review_comments=metrics_data["review_comments"],
        languages=metrics_data["languages"],
        issues=metrics_data["issues"],
        reviews=metrics_data["reviews"],
        lifetime_commits=metrics_data["lifetime_commits"],
        active_years=metrics_data["active_years"],
        projects=metrics_data["projects"],
    )

    return Card(
        username=signals.username,
        name=signals.name,
        avatar_url=signals.avatar_url,
        bio=signals.bio,
        location=signals.location,
        stats=final_stats,
        overall=overall,
        base_ovr=base_ovr,
        position=pos,
        family=family,
        finish=finish,
        tier=CARD_TIERS[finish],
        archetype=arch,
        archetype_blurb=blurb,
        top_language=top_lang,
        metrics=metrics_obj,
        playstyles=playstyles,
    )
