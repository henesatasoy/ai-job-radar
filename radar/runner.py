from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from . import fetchers, llm
from .config import Settings
from .filtering import classify, too_old
from .models import Job
from .state import State, now_iso
from .telegram import Telegram, esc, format_digest, format_job
from .textutil import title_key

log = logging.getLogger(__name__)

TR_TZ = timezone(timedelta(hours=3))  # Turkey has no DST


# ------------------------------------------------------------------ fetching

def _resolve_auto(src: dict, state: State) -> tuple[dict | None, list[Job] | None]:
    """For type: auto sources, find which ATS + slug works and cache it."""
    name = src["name"]
    cache = state.data["auto"].get(name) or {}
    if cache.get("type"):
        return dict(src, type=cache["type"], slug=cache["slug"]), None
    last = cache.get("last_probe")
    if last and datetime.fromisoformat(last) > datetime.now(timezone.utc) - timedelta(hours=24):
        return None, None  # probed recently and found nothing
    found = fetchers.probe(src)
    if not found:
        state.data["auto"][name] = {"last_probe": now_iso()}
        return None, None
    t, slug, jobs = found
    state.data["auto"][name] = {"type": t, "slug": slug, "found": now_iso()}
    log.info("auto: %s -> %s/%s", name, t, slug)
    return dict(src, type=t, slug=slug), jobs


def fetch_one(src: dict) -> list[Job]:
    fn = fetchers.FETCHERS.get(src["type"])
    if not fn:
        raise ValueError(f"unknown source type {src['type']}")
    jobs = fn(src)
    # drop obviously broken rows
    return [j for j in jobs if j.native_id and j.url]


def fetch_all(sources: list[dict], state: State, workers: int = 8) -> dict[str, tuple[dict, list[Job] | Exception]]:
    resolved: list[tuple[dict, list[Job] | None]] = []
    results: dict[str, tuple[dict, list[Job] | Exception]] = {}
    for s in sources:
        if s["type"] == "auto":
            r, pre = _resolve_auto(s, state)
            if r is None:
                continue  # not found yet, nothing to report as failure
            resolved.append((r, pre))
        else:
            resolved.append((s, None))

    def work(item):
        src, pre = item
        t0 = time.time()
        try:
            jobs = pre if pre is not None else fetch_one(src)
            log.info("%-22s %4d jobs  %.1fs", src["name"], len(jobs), time.time() - t0)
            return src, jobs
        except Exception as e:  # noqa: BLE001
            log.warning("%-22s FAILED %s: %s", src["name"], type(e).__name__, str(e)[:200])
            return src, e

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for src, res in ex.map(work, resolved):
            results[src["name"]] = (src, res)
    return results


# ------------------------------------------------------------------ main run

def run(cfg: Settings, mode: str = "run") -> int:
    dry = mode == "dry"
    state = State(cfg.state_path)
    tg = Telegram(cfg.telegram_token, cfg.telegram_chat_id, dry=dry)
    prof = cfg.profile
    rules = prof.get("rules") or {}

    if mode == "test":
        return run_test(cfg, state, tg)

    results = fetch_all(cfg.sources, state)
    alerts: list[str] = []
    candidates: list[tuple[dict, Job]] = []
    alert_after = int(rules.get("alert_after_failures", 16))

    for name, (src, res) in results.items():
        if isinstance(res, Exception):
            streak = state.record_fail(name, f"{type(res).__name__}: {res}")
            if streak == alert_after:
                alerts.append(f"⚠️ {esc(name)} {streak} kez üst üste okunamadı. {esc(str(res)[:150])}")
            # a cached auto slug that stops working gets re-probed next time
            if name in state.data["auto"] and streak >= 4:
                state.data["auto"].pop(name, None)
            continue

        jobs = res
        state.record_ok(name, len(jobs))
        if not jobs:
            continue
        by_id = {j.native_id: j for j in jobs}
        seen = state.seen_ids(name)

        if not state.is_seeded(name):
            state.mark_seen(name, by_id.keys())
            state.set_seeded(name)
            state.touch_title_keys(title_key(j.company, j.title, j.location) for j in jobs)
            log.info("%s seeded with %d jobs (no alerts on first run)", name, len(by_id))
            continue

        new = [j for i, j in by_id.items() if i not in seen]
        reset_min = int(rules.get("reset_min_new", 40))
        if len(new) >= reset_min and len(new) > 0.6 * len(by_id):
            # most likely the site changed its URL or id format
            alerts.append(f"ℹ️ {esc(name)} kaynağında {len(new)} ilan birden yeni göründü. Site yapısı değişmiş olabilir, sessizce kaydettim.")
            state.mark_seen(name, by_id.keys())
            continue

        state.mark_seen(name, by_id.keys())
        candidates.extend((src, j) for j in new)

    log.info("new postings this run: %d", len(candidates))
    to_send = process_candidates(candidates, cfg, state, alerts)
    send_all(tg, to_send, prof)

    for a in alerts:
        tg.send(a)

    maybe_daily_report(cfg, state, tg, results)

    if not dry:
        state.prune(int(rules.get("forget_after_days", 60)))
        state.save()
    return 0


def process_candidates(candidates, cfg: Settings, state: State, alerts: list[str]) -> list[Job]:
    prof = cfg.profile
    rules = prof.get("rules") or {}
    max_age = rules.get("max_age_days", 10)
    enrich_cap = int(rules.get("max_page_fetches", 25))
    repost_days = int(rules.get("repost_window_days", 30))

    shortlist: list[Job] = []
    hot_ids: set[str] = set()
    kw_ids: set[str] = set()
    for src, job in candidates:
        if too_old(job, max_age):
            log.info("old, skipped: %s", job.short())
            continue
        if src.get("enrich") and enrich_cap > 0:
            enrich_cap -= 1
            try:
                fetchers.enrich(job)
            except Exception as e:  # noqa: BLE001
                log.info("enrich failed %s: %s", job.url, e)
        verdict, why = classify(job, prof, src)
        log.info("%-5s %s  (%s)", verdict, job.short(), why)
        if verdict == "drop":
            continue
        key = title_key(job.company, job.title, job.location)
        job.is_repost = state.repost_seen_recently(key, repost_days)
        if verdict == "hot":
            hot_ids.add(job.key)
        if why != "genel":
            kw_ids.add(job.key)
        job.reason = why
        shortlist.append(job)

    state.touch_title_keys(title_key(j.company, j.title, j.location) for _, j in candidates)

    if prof.get("skip_reposts"):
        shortlist = [j for j in shortlist if not j.is_repost or j.key in hot_ids]

    if not shortlist:
        return []

    min_score = int(prof.get("min_score", 60))
    use_llm = llm.enabled(cfg.llm)
    if use_llm:
        try:
            llm.score(shortlist, cfg.cv_text, prof, cfg.llm)
        except Exception as e:  # noqa: BLE001
            use_llm = False
            alerts.append(f"⚠️ Yapay zeka puanlaması çalışmadı, kelime filtresiyle gönderiyorum. {esc(str(e)[:120])}")

    out = []
    for j in shortlist:
        if j.key in hot_ids:
            out.append(j)
        elif use_llm and j.score is not None:
            if j.score >= min_score:
                out.append(j)
        elif j.key in kw_ids or not prof.get("include"):
            # no LLM result, fall back to keyword match
            j.score = None
            j.reason = f"kelime eşleşti {j.reason}"
            out.append(j)
    out.sort(key=lambda j: (j.key not in hot_ids, -(j.score or 0)))
    return out


def send_all(tg: Telegram, jobs: list[Job], prof: dict):
    if not jobs:
        return
    rules = prof.get("rules") or {}
    cap = int(rules.get("max_messages_per_run", 12))
    for j in jobs[:cap]:
        tg.send(format_job(j))
    rest = jobs[cap:]
    if rest:
        for m in format_digest(rest, f"+{len(rest)} ilan daha"):
            tg.send(m)


# ------------------------------------------------------------------ reports

def maybe_daily_report(cfg: Settings, state: State, tg: Telegram, results, force: bool = False):
    hour = int((cfg.profile.get("rules") or {}).get("daily_report_hour", 10))
    now_tr = datetime.now(TR_TZ)
    day = now_tr.date().isoformat()
    if not force and (state.data.get("last_report") == day or now_tr.hour < hour):
        return
    state.data["last_report"] = day
    tg.send(health_text(cfg, state, results, title="📊 Günlük durum"))


def health_text(cfg: Settings, state: State, results, title: str) -> str:
    ok, bad, empty = [], [], []
    for s in cfg.sources:
        name = s["name"]
        h = state.health(name)
        res = results.get(name, (None, None))[1] if results else None
        if isinstance(res, Exception) or (res is None and h.get("fail_streak")):
            bad.append(f"{name} ({h.get('fail_streak', 1)} kez)")
        elif res is None and s["type"] == "auto" and not state.data["auto"].get(name, {}).get("type"):
            empty.append(f"{name} (adres bulunamadı)")
        elif isinstance(res, list) and not res:
            empty.append(name)
        elif res is not None or h.get("last_ok"):
            ok.append(name)
    sent = state.sent_since(24)
    lines = [f"<b>{title}</b>", f"✅ {len(ok)}/{len(cfg.sources)} kaynak çalışıyor"]
    if bad:
        lines.append("❌ Okunamayan  " + esc(", ".join(bad)))
    if empty:
        lines.append("📭 İlan dönmeyen  " + esc(", ".join(empty)))
    lines.append(f"📬 Son 24 saatte {len(sent)} ilan gönderildi")
    if not llm.enabled(cfg.llm):
        lines.append("🤖 Yapay zeka anahtarı yok, sadece kelime filtresi açık")
    if not cfg.cv_text:
        lines.append("📄 CV eklenmemiş")
    return "\n".join(lines)


def run_test(cfg: Settings, state: State, tg: Telegram) -> int:
    """Manual check. Reads every source, shows counts, scores a few current jobs.
    Does not change the saved state."""
    tg.send("🧪 Test başladı. Kaynaklar okunuyor…")
    results = fetch_all(cfg.sources, state)
    rows = []
    sample: list[tuple[dict, Job]] = []
    for s in cfg.sources:
        name = s["name"]
        if name not in results:
            rows.append(f"➖ {esc(name)}  adres bulunamadı")
            continue
        src, res = results[name]
        if isinstance(res, Exception):
            rows.append(f"❌ {esc(name)}  {esc(type(res).__name__)} {esc(str(res)[:80])}")
        else:
            how = f" ({src['type']}/{src['slug']})" if s["type"] == "auto" else ""
            rows.append(f"{'✅' if res else '📭'} {esc(name)}  {len(res)} ilan{esc(how)}")
            sample.extend((src, j) for j in res)
    for m in format_digest_lines(rows, "Kaynak durumu"):
        tg.send(m)

    # run the filter on everything currently listed, show the best few
    prof = cfg.profile
    kept = []
    for src, j in sample:
        v, why = classify(j, prof, src)
        if v != "drop":
            j.reason = why
            kept.append((v, j))
    hot = [j for v, j in kept if v == "hot"]
    pool = hot[:6] + [j for v, j in kept if v == "keep"][: 18 - min(len(hot), 6)]
    if pool and llm.enabled(cfg.llm):
        try:
            llm.score(pool, cfg.cv_text, prof, cfg.llm)
        except Exception as e:  # noqa: BLE001
            tg.send(f"⚠️ Yapay zeka testi başarısız. {esc(str(e)[:200])}")
    pool.sort(key=lambda j: -(j.score or 0))
    tg.send(f"Filtreden geçen mevcut ilan sayısı {len(kept)} (Türkçe vb. öncelikli {len(hot)}). En iyi 5 örnek aşağıda.")
    for j in pool[:5]:
        tg.send(format_job(j))
    tg.send(health_text(cfg, state, results, title="🧪 Test bitti"))
    return 0


def format_digest_lines(rows: list[str], title: str) -> list[str]:
    msgs, cur = [], f"<b>{esc(title)}</b>\n"
    for r in rows:
        if len(cur) + len(r) + 1 > 4000:
            msgs.append(cur)
            cur = ""
        cur += r + "\n"
    msgs.append(cur)
    return msgs
