from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _deep_merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in (b or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


@dataclass
class Settings:
    sources: list[dict]
    profile: dict
    cv_text: str = ""
    telegram_token: str = ""
    telegram_chat_id: str = ""
    llm: dict = field(default_factory=dict)
    state_path: Path = ROOT / "state" / "state.json"


def load(root: Path = ROOT) -> Settings:
    src_doc = yaml.safe_load((root / "config" / "sources.yaml").read_text("utf-8")) or {}
    defaults = src_doc.get("defaults") or {}
    sources = []
    for s in src_doc.get("sources") or []:
        merged = dict(defaults, **s)
        if merged.get("enabled", True):
            sources.append(merged)

    # Sources kept out of the public repo. GitHub Secret PRIVATE_SOURCES holds a YAML list
    # in the same format as sources.yaml. They are marked private so logs and state stay masked.
    private = os.environ.get("PRIVATE_SOURCES", "").strip()
    if private:
        doc = yaml.safe_load(private) or []
        if isinstance(doc, dict):
            doc = doc.get("sources") or []
        for s in doc:
            merged = dict(defaults, **s, private=True)
            if merged.get("enabled", True):
                sources.append(merged)

    profile = yaml.safe_load((root / "config" / "profile.yaml").read_text("utf-8")) or {}
    # Optional private override kept in a GitHub Secret
    secret_profile = os.environ.get("PROFILE_YAML", "").strip()
    if secret_profile:
        profile = _deep_merge(profile, yaml.safe_load(secret_profile) or {})

    cv = os.environ.get("CV_TEXT", "").strip()
    cv_file = root / "config" / "cv.local.txt"  # for local testing only, git ignored
    if not cv and cv_file.exists():
        cv = cv_file.read_text("utf-8").strip()

    llm = {
        "provider": (os.environ.get("LLM_PROVIDER") or "gemini").strip().lower(),
        "api_key": os.environ.get("LLM_API_KEY", "").strip(),
        "model": os.environ.get("LLM_MODEL", "").strip(),
        "base_url": os.environ.get("LLM_BASE_URL", "").strip(),
    }

    return Settings(
        sources=sources,
        profile=profile,
        cv_text=cv,
        telegram_token=os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", "").strip(),
        llm=llm,
        state_path=root / "state" / "state.json",
    )
