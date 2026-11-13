from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

STATE_VERSION = 1


def today() -> str:
    return date.today().isoformat()


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class State:
    """Small JSON store committed back to the repo after each run.

    seen[source][job_id] = last day the job was still listed
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = {
            "version": STATE_VERSION,
            "seen": {},
            "sources": {},
            "title_keys": {},
            "auto": {},
            "last_report": "",
            "sent_log": [],
        }
        if self.path.exists():
            loaded = json.loads(self.path.read_text("utf-8") or "{}")
            self.data.update(loaded)

    # ---- seen ids
    def is_seeded(self, source: str) -> bool:
        return bool(self.data["sources"].get(source, {}).get("seeded"))

    def seen_ids(self, source: str) -> dict:
        return self.data["seen"].setdefault(source, {})

    def mark_seen(self, source: str, ids, day: str | None = None):
        d = day or today()
        s = self.seen_ids(source)
        for i in ids:
            s[i] = d

    def set_seeded(self, source: str):
        self.data["sources"].setdefault(source, {})["seeded"] = True

    # ---- reposts
    def repost_seen_recently(self, key: str, days: int) -> bool:
        last = self.data["title_keys"].get(key)
        if not last:
            return False
        try:
            return (date.today() - date.fromisoformat(last)).days <= days
        except ValueError:
            return False

    def touch_title_keys(self, keys):
        d = today()
        for k in keys:
            self.data["title_keys"][k] = d

    # ---- health
    def record_ok(self, source: str, count: int):
        h = self.data["sources"].setdefault(source, {})
        h.update(last_ok=now_iso(), last_count=count, fail_streak=0, last_error="")

    def record_fail(self, source: str, err: str) -> int:
        h = self.data["sources"].setdefault(source, {})
        h["fail_streak"] = int(h.get("fail_streak", 0)) + 1
        h["last_error"] = err[:300]
        h["last_fail"] = now_iso()
        return h["fail_streak"]

    def health(self, source: str) -> dict:
        return self.data["sources"].get(source, {})

    # ---- log of sent messages, used by the daily report
    def log_sent(self, items: list[dict]):
        self.data["sent_log"] = (self.data["sent_log"] + items)[-300:]

    def sent_since(self, hours: int) -> list[dict]:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        out = []
        for it in self.data["sent_log"]:
            try:
                if datetime.fromisoformat(it["t"]) >= cutoff:
                    out.append(it)
            except (KeyError, ValueError):
                pass
        return out

    # ---- housekeeping
    def prune(self, keep_days: int = 60):
        cutoff = (date.today() - timedelta(days=keep_days)).isoformat()
        for src, ids in self.data["seen"].items():
            for k in [k for k, d in ids.items() if d < cutoff]:
                del ids[k]
        tk = self.data["title_keys"]
        cutoff_tk = (date.today() - timedelta(days=max(keep_days, 90))).isoformat()
        for k in [k for k, d in tk.items() if d < cutoff_tk]:
            del tk[k]

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1, sort_keys=True), "utf-8")
        os.replace(tmp, self.path)
