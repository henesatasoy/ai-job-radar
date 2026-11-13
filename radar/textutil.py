from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from datetime import datetime, timezone
from typing import Optional

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_SCRIPT_RE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.S | re.I)


def strip_html(text: str | None, limit: int | None = None) -> str:
    if not text:
        return ""
    # Greenhouse sends HTML that is itself entity escaped, so unescape first
    text = html.unescape(text)
    text = _SCRIPT_RE.sub(" ", text)
    text = re.sub(r"<\s*(br|/p|/li|/h\d|/div)\s*/?>", "\n", text, flags=re.I)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    if limit and len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + " …"
    return text


def clean(text: str | None) -> str:
    return _WS_RE.sub(" ", html.unescape(text or "")).strip()


def fold(text: str | None) -> str:
    """Lowercase, strip accents. Turkish dotted/dotless i folded to i."""
    t = (text or "").replace("İ", "i").replace("I", "i").replace("ı", "i")
    t = unicodedata.normalize("NFKD", t.lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def slug_to_title(slug: str) -> str:
    slug = re.sub(r"[-_]+", " ", slug)
    slug = re.sub(r"\s+", " ", slug).strip()
    return slug[:1].upper() + slug[1:] if slug else slug


def title_key(company: str, title: str, location: str = "") -> str:
    t = fold(f"{company}|{title}|{location}")
    t = re.sub(r"[^a-z0-9|]+", " ", t)
    t = re.sub(r"\b([a-z]{3,})s\b", r"\1", t)  # experts == expert
    t = _WS_RE.sub(" ", t).strip()
    # stored in the public state file, so keep only a hash
    return hashlib.sha1(t.encode("utf-8")).hexdigest()[:16]


def parse_dt(value) -> Optional[datetime]:
    """Accept ISO strings, epoch seconds or epoch ms."""
    if value in (None, "", 0):
        return None
    try:
        if isinstance(value, (int, float)):
            v = float(value)
            if v > 1e12:
                v /= 1000.0
            return datetime.fromtimestamp(v, tz=timezone.utc)
        s = str(value).strip()
        if s.isdigit():
            return parse_dt(int(s))
        s = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, OSError, OverflowError):
        return None


# ---------- pay parsing ----------

_NUM = r"(\d{1,3}(?:[.,]\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s*(k)?"
_CUR = r"(?:\$|usd\s?|us\$|€|eur\s?|£|gbp\s?)"
_HOURLY = r"(?:/\s*h(?:ou)?r\b|/\s*hour|per\s+hour|an\s+hour|hourly|/\s*saat|saatlik)"
_RANGE_RE = re.compile(
    rf"{_CUR}\s*{_NUM}(?:\s*(?:-|–|—|to)\s*{_CUR}?\s*{_NUM})?\s*(?:usd)?\s*{_HOURLY}",
    re.I,
)


def _to_float(num: str, k: str | None) -> float:
    num = num.replace(",", "")
    v = float(num)
    if k:
        v *= 1000
    return v


def parse_hourly(text: str | None) -> Optional[tuple[float, float]]:
    """Find an hourly pay range like '$25-40/hr' or 'USD 30 per hour'.

    Returns (low, high) in the posting's currency, or None.
    """
    if not text:
        return None
    m = _RANGE_RE.search(text)
    if not m:
        return None
    lo = _to_float(m.group(1), m.group(2))
    hi = _to_float(m.group(3), m.group(4)) if m.group(3) else lo
    if lo > hi:
        lo, hi = hi, lo
    # sanity check, hourly pay above 1000 is likely a parse error
    if hi <= 0 or hi > 1000:
        return None
    return lo, hi


def fmt_pay(rng: Optional[tuple[float, float]]) -> str:
    if not rng:
        return ""
    lo, hi = rng
    f = lambda v: f"{v:.0f}" if v == int(v) else f"{v:.2f}".rstrip("0").rstrip(".")
    return f"${f(lo)}/saat" if lo == hi else f"${f(lo)}-{f(hi)}/saat"
