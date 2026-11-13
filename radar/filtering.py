"""Cheap keyword filter that runs before the LLM.

Result for each job is one of
  "drop"  clearly not for the user
  "keep"  worth scoring
  "hot"   always notify (for example Turkish language roles)
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from .models import Job
from .textutil import fmt_pay, fold, parse_hourly


def _any(words, text: str) -> str | None:
    for w in words or []:
        if fold(w) in text:
            return w
    return None


def _word_any(words, text: str) -> str | None:
    """Like _any but matches whole words, so 'us' does not match 'campus'."""
    for w in words or []:
        if re.search(rf"(?<![a-z0-9]){re.escape(fold(w))}(?![a-z0-9])", text):
            return w
    return None


def classify(job: Job, profile: dict, src: dict) -> tuple[str, str]:
    title = fold(job.title)
    loc = fold(job.location)
    head = fold(job.description[:1500])
    everything = f"{title} | {loc} | {head}"

    # 1. hot words, for example Turkish. They skip the score check later,
    #    but hard rules below (field, location, pay) still apply.
    hot = _any(profile.get("always_include"), f"{title} | {loc}")

    # 2. title blacklist
    hit = _any(profile.get("exclude_title"), title)
    if hit:
        return "drop", f"başlıkta '{hit}'"

    # 3. location rules
    lrules = profile.get("location") or {}
    hit = _any(lrules.get("block"), f"{loc} | {head}")
    if hit:
        return "drop", f"konum kısıtı '{hit}'"
    has_ok_place = _any(lrules.get("ok_places"), loc)
    place = _word_any(lrules.get("block_places"), loc)
    if place and not has_ok_place:
        return "drop", f"sadece {place}"
    if loc and lrules.get("allowed_only_if_remote"):
        is_remote = job.remote or _any(lrules.get("remote_words"), loc)
        onsite = _any(lrules.get("onsite_words"), loc)
        if onsite and not has_ok_place and not is_remote:
            return "drop", "ofiste çalışma"

    # 4. pay floor
    floor = profile.get("min_hourly_usd")
    rng = parse_hourly(job.pay) or parse_hourly(job.description[:4000]) or parse_hourly(job.title)
    if rng:
        job.pay = job.pay or fmt_pay(rng)
        if floor and rng[1] < float(floor):
            return "drop", f"ücret düşük ({fmt_pay(rng)})"

    if hot:
        return "hot", f"'{hot}' geçiyor"

    # 5. sources with many unrelated roles (AI labs) must match an include word
    if src.get("require_include"):
        hit = _any(profile.get("include"), title)
        if not hit:
            return "drop", "ilgi kelimesi yok"
        return "keep", f"'{hit}'"

    hit = _any(profile.get("include"), everything)
    return "keep", (f"'{hit}'" if hit else "genel")


def too_old(job: Job, max_age_days: int | None) -> bool:
    if not max_age_days or not job.posted_at:
        return False
    return job.posted_at < datetime.now(timezone.utc) - timedelta(days=max_age_days)
