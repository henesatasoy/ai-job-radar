"""Scores jobs against the CV with a free tier LLM.

Supported providers
  gemini   Google AI Studio key (default)
  openai   any OpenAI compatible API, e.g. Groq or OpenRouter. Set LLM_BASE_URL.
"""
from __future__ import annotations

import json
import logging
import re

from . import net
from .models import Job

log = logging.getLogger(__name__)

DEFAULT_MODELS = {
    "gemini": "gemini-flash-latest",
    "openai": "llama-3.3-70b-versatile",
}

PROMPT = """You screen remote job postings for one candidate and decide which ones are worth his time.

CANDIDATE CV
<<<
{cv}
>>>

CANDIDATE PREFERENCES
<<<
{prefs}
>>>

Rules
- He lives in Turkey and works remotely. A job that only accepts residents of other countries is not eligible.
- Turkish language roles are a strong plus.
- If the post asks for a degree, licence or seniority he clearly does not have, score low.
- If pay is visible and below his minimum, score low.
- Titles may be auto generated from URL slugs. Judge by meaning.

JOBS
{jobs}

Return only JSON in this shape
{{"results": [{{"id": "<job id>", "score": 0-100, "eligible": true/false, "reason": "<at most 18 words, simple Turkish, no dashes, no semicolons, no colons>"}}]}}
Give one result for every job id."""


def _prefs_text(profile: dict) -> str:
    parts = []
    if profile.get("min_hourly_usd"):
        parts.append(f"Minimum pay about ${profile['min_hourly_usd']}/hour.")
    if profile.get("languages"):
        parts.append("Languages " + ", ".join(profile["languages"]) + ".")
    if profile.get("interests"):
        parts.append("Interests and notes " + str(profile["interests"]).strip())
    if profile.get("avoid"):
        parts.append("Avoid " + str(profile["avoid"]).strip())
    return "\n".join(parts) or "No extra preferences."


def _jobs_text(jobs: list[Job], desc_chars: int) -> str:
    rows = []
    for j in jobs:
        rows.append(
            json.dumps(
                {
                    "id": j.key,
                    "company": j.company,
                    "title": j.title,
                    "location": j.location,
                    "pay": j.pay,
                    "description": (j.description or "")[:desc_chars],
                },
                ensure_ascii=False,
            )
        )
    return "\n".join(rows)


GEMINI_FALLBACKS = ["gemini-flash-latest", "gemini-2.5-flash", "gemini-2.0-flash", "gemini-flash-lite-latest"]


def _call_gemini(cfg: dict, prompt: str) -> str:
    models = [cfg["model"]] if cfg.get("model") else []
    models += [m for m in GEMINI_FALLBACKS if m not in models]
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
    }
    last = None
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        try:
            data = net.post_json(url, body, headers={"x-goog-api-key": cfg["api_key"]}, timeout=90)
        except Exception as e:  # noqa: BLE001
            resp = getattr(e, "response", None)
            if resp is not None and resp.status_code == 404:
                last = e
                continue  # model name not available, try the next one
            raise
        cfg["model"] = model  # remember the one that worked
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts)
    raise last or RuntimeError("no gemini model available")


def _call_openai(cfg: dict, prompt: str) -> str:
    base = (cfg.get("base_url") or "https://api.groq.com/openai/v1").rstrip("/")
    model = cfg.get("model") or DEFAULT_MODELS["openai"]
    body = {
        "model": model,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": prompt}],
    }
    data = net.post_json(
        f"{base}/chat/completions", body, headers={"Authorization": f"Bearer {cfg['api_key']}"}, timeout=90
    )
    return data["choices"][0]["message"]["content"]


def parse_results(text: str) -> dict[str, dict]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise
        data = json.loads(m.group(0))
    items = data.get("results", data) if isinstance(data, dict) else data
    out = {}
    for it in items or []:
        if not isinstance(it, dict) or "id" not in it:
            continue
        try:
            score = int(round(float(it.get("score", 0))))
        except (TypeError, ValueError):
            score = 0
        if it.get("eligible") is False:
            score = min(score, 20)
        out[str(it["id"])] = {"score": max(0, min(100, score)), "reason": str(it.get("reason", "")).strip()}
    return out


def enabled(cfg: dict) -> bool:
    return bool(cfg.get("api_key"))


def score(jobs: list[Job], cv: str, profile: dict, cfg: dict, batch: int = 8) -> None:
    """Sets job.score and job.reason in place. Raises if every call fails."""
    if not jobs:
        return
    call = _call_gemini if cfg.get("provider", "gemini") == "gemini" else _call_openai
    desc_chars = int((profile.get("rules") or {}).get("llm_desc_chars", 1500))
    errors, first_err = 0, ""
    for i in range(0, len(jobs), batch):
        chunk = jobs[i : i + batch]
        prompt = PROMPT.format(
            cv=(cv or "(CV not provided yet)")[:12000],
            prefs=_prefs_text(profile),
            jobs=_jobs_text(chunk, desc_chars),
        )
        try:
            res = parse_results(call(cfg, prompt))
        except Exception as e:
            errors += 1
            first_err = first_err or f"{type(e).__name__}: {e}"
            log.warning("LLM call failed: %s", e)
            continue
        for j in chunk:
            r = res.get(j.key)
            if r:
                j.score, j.reason = r["score"], r["reason"]
    if errors and errors * batch >= len(jobs):
        raise RuntimeError(f"all LLM calls failed. {first_err[:300]}")
