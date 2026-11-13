from __future__ import annotations

import html
import logging
import time

from . import net
from .models import Job

log = logging.getLogger(__name__)

MAX_LEN = 4000


def esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def format_job(job: Job) -> str:
    lines = [f"🆕 <b>{esc(job.title)}</b>"]
    meta = f"🏢 {esc(job.company or job.source)}"
    if job.location:
        meta += f" · 📍 {esc(job.location[:120])}"
    lines.append(meta)
    if job.pay:
        lines.append(f"💰 {esc(job.pay[:120])}")
    if job.score is not None:
        lines.append(f"⭐ {job.score}/100  {esc(job.reason)}")
    elif job.reason:
        lines.append(f"🔎 {esc(job.reason)}")
    if job.is_repost:
        lines.append("🔁 Bu başlık son günlerde de açılmıştı")
    lines.append(f'<a href="{html.escape(job.url, quote=True)}">İlana git</a>')
    return "\n".join(lines)


def format_digest(jobs: list[Job], title: str) -> list[str]:
    msgs, cur = [], f"<b>{esc(title)}</b>\n"
    for j in jobs:
        sc = f" ⭐{j.score}" if j.score is not None else ""
        line = f'• <a href="{html.escape(j.url, quote=True)}">{esc(j.title[:90])}</a> ({esc(j.company or j.source)}){sc}\n'
        if len(cur) + len(line) > MAX_LEN:
            msgs.append(cur)
            cur = ""
        cur += line
    if cur.strip():
        msgs.append(cur)
    return msgs


class Telegram:
    def __init__(self, token: str, chat_id: str, dry: bool = False):
        self.token, self.chat_id, self.dry = token, chat_id, dry or not (token and chat_id)
        if self.dry and not dry:
            log.warning("Telegram token or chat id missing, messages will be printed only")

    def send(self, text: str) -> bool:
        if len(text) > MAX_LEN:
            text = text[: MAX_LEN - 1] + "…"
        if self.dry:
            print("---- TELEGRAM ----\n" + text + "\n------------------")
            return True
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        for attempt in range(4):
            try:
                r = net.session().post(url, json=payload, timeout=20)
            except Exception as e:
                log.warning("telegram error %s", e)
                time.sleep(2)
                continue
            if r.status_code == 429:
                wait = int((r.json().get("parameters") or {}).get("retry_after", 3))
                time.sleep(min(wait, 30) + 1)
                continue
            if r.ok:
                time.sleep(0.4)  # stay under the per chat rate limit
                return True
            log.warning("telegram %s: %s", r.status_code, r.text[:200])
            if r.status_code == 400 and payload.get("parse_mode"):
                # broken HTML, retry as plain text
                payload.pop("parse_mode")
                continue
            return False
        return False
