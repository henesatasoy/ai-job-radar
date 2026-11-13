from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Job:
    source: str            # source name from sources.yaml, e.g. "Mercor"
    native_id: str         # id inside that source
    title: str
    url: str
    company: str = ""
    location: str = ""
    remote: Optional[bool] = None
    posted_at: Optional[datetime] = None
    description: str = ""  # plain text, may be empty
    pay: str = ""          # free text pay info if the source gives it
    extra: dict = field(default_factory=dict)

    # filled later in the pipeline
    score: Optional[int] = None
    reason: str = ""
    is_repost: bool = False

    @property
    def key(self) -> str:
        return f"{self.source}::{self.native_id}"

    def short(self) -> str:
        return f"[{self.source}] {self.title} ({self.location or '-'}) {self.url}"
