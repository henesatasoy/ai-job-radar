from datetime import datetime, timedelta, timezone

import pytest
import yaml

from radar import config, fetchers, llm, runner
from radar.filtering import classify, too_old
from radar.models import Job
from radar.telegram import format_job
from radar.textutil import fmt_pay, fold, parse_hourly

ROOT = config.ROOT
PROFILE = yaml.safe_load((ROOT / "config" / "profile.yaml").read_text("utf-8"))


def J(**kw):
    base = dict(source="S", native_id="1", title="AI Trainer", url="https://x/1", company="S")
    base.update(kw)
    return Job(**base)


# ---------------------------------------------------------------- text

@pytest.mark.parametrize(
    "text,expected",
    [
        ("Pay $25-40/hr", (25, 40)),
        ("USD 30 per hour", (30, 30)),
        ("approximately $2.4 per hour", (2.4, 2.4)),
        ("$15 – $20 hourly", (15, 20)),
        ("salary $120k per year", None),
        ("", None),
    ],
)
def test_parse_hourly(text, expected):
    assert parse_hourly(text) == expected


def test_fmt_and_fold():
    assert fmt_pay((25, 40)) == "$25-40/saat"
    assert fold("TÜRKÇE İstanbul") == "turkce istanbul"


# ---------------------------------------------------------------- filter

def test_turkish_is_hot():
    v, _ = classify(J(title="Search Quality Rater - Türkçe"), PROFILE, {})
    assert v == "hot"


def test_exclude_title():
    v, _ = classify(J(title="Enterprise Account Executive"), PROFILE, {})
    assert v == "drop"


def test_us_only_dropped():
    v, why = classify(J(title="AI Tutor", location="United States", description="US only. Must reside in the US."), PROFILE, {})
    assert v == "drop" and "konum" in why


def test_onsite_dropped_but_remote_ok():
    assert classify(J(title="Annotator", location="London (on-site)"), PROFILE, {})[0] == "drop"
    assert classify(J(title="Annotator", location="Remote, EMEA"), PROFILE, {})[0] == "keep"


def test_pay_floor():
    prof = dict(PROFILE, min_hourly_usd=20)
    v, why = classify(J(title="Rater", description="Pay rate approximately $6 per hour"), prof, {})
    assert v == "drop" and "ücret" in why
    j = J(title="Rater", description="Pay $25-35/hr")
    assert classify(j, prof, {})[0] == "keep"
    assert j.pay == "$25-35/saat"


def test_require_include_for_labs():
    assert classify(J(title="Software Engineer, Infra"), PROFILE, {"require_include": True})[0] == "drop"
    assert classify(J(title="AI Tutor, Chemistry"), PROFILE, {"require_include": True})[0] == "keep"


def test_too_old():
    old = J(posted_at=datetime.now(timezone.utc) - timedelta(days=30))
    assert too_old(old, 10) and not too_old(J(), 10)


# ---------------------------------------------------------------- llm + telegram

def test_llm_parse_results():
    txt = '```json\n{"results":[{"id":"S::1","score":"85","eligible":true,"reason":"uygun"},{"id":"S::2","score":90,"eligible":false,"reason":"ABD"}]}\n```'
    r = llm.parse_results(txt)
    assert r["S::1"]["score"] == 85
    assert r["S::2"]["score"] == 20


def test_format_job_escapes():
    j = J(title="<b>Rater</b> & co", url='https://x/?a=1&b="2"', score=80, reason="iyi", pay="$20/saat")
    msg = format_job(j)
    assert "&lt;b&gt;Rater&lt;/b&gt; &amp; co" in msg
    assert 'href="https://x/?a=1&amp;b=&quot;2&quot;"' in msg


# ---------------------------------------------------------------- full runs

class FakeTG:
    def __init__(self):
        self.msgs = []

    def send(self, text):
        self.msgs.append(text)
        return True


@pytest.fixture
def env(tmp_path, monkeypatch):
    listing = {"jobs": [J(native_id="1", title="Turkish AI Tutor", location="Remote")]}
    calls = {"llm": 0}

    def fake_fetch(src):
        if src.get("boom"):
            raise RuntimeError("site down")
        return list(listing["jobs"])

    monkeypatch.setitem(fetchers.FETCHERS, "fake", fake_fetch)
    tg = FakeTG()
    monkeypatch.setattr(runner, "Telegram", lambda *a, **k: tg)

    def fake_score(jobs, cv, prof, cfg, batch=8):
        calls["llm"] += 1
        for j in jobs:
            j.score = 90 if "tutor" in j.title.lower() else 30
            j.reason = "test"

    monkeypatch.setattr(llm, "score", fake_score)

    prof = dict(PROFILE)
    prof["rules"] = dict(PROFILE["rules"], daily_report_hour=99)  # no daily report in tests
    cfg = config.Settings(
        sources=[{"name": "S", "type": "fake"}],
        profile=prof,
        cv_text="cv",
        llm={"provider": "gemini", "api_key": "k"},
        state_path=tmp_path / "state.json",
    )
    return cfg, listing, tg, calls


def test_first_run_seeds_then_alerts_new(env):
    cfg, listing, tg, calls = env
    runner.run(cfg)
    assert tg.msgs == []  # first run only seeds

    listing["jobs"].append(J(native_id="2", title="Data Annotator", location="Remote"))
    listing["jobs"].append(J(native_id="3", title="Turkish Writer", location="Remote"))
    listing["jobs"].append(J(native_id="4", title="Senior Account Executive", location="Remote"))
    runner.run(cfg)
    joined = "\n".join(tg.msgs)
    assert "Turkish Writer" in joined          # hot
    assert "Data Annotator" not in joined      # llm score 30 < 60
    assert "Account Executive" not in joined   # dropped by keyword
    assert calls["llm"] == 1

    tg.msgs.clear()
    runner.run(cfg)
    assert tg.msgs == []  # nothing new


def test_failure_alert_once(env):
    cfg, listing, tg, calls = env
    cfg.sources = [{"name": "S", "type": "fake", "boom": True}]
    cfg.profile["rules"]["alert_after_failures"] = 3
    for _ in range(5):
        runner.run(cfg)
    alerts = [m for m in tg.msgs if "üst üste" in m]
    assert len(alerts) == 1


def test_reset_guard(env):
    cfg, listing, tg, calls = env
    runner.run(cfg)
    listing["jobs"] = [J(native_id=f"n{i}", title=f"Turkish Job {i}") for i in range(50)]
    runner.run(cfg)
    assert len(tg.msgs) == 1 and "sessizce" in tg.msgs[0]


def test_llm_failure_falls_back_to_keywords(env, monkeypatch):
    cfg, listing, tg, calls = env
    runner.run(cfg)

    def broken(*a, **k):
        raise RuntimeError("quota")

    monkeypatch.setattr(llm, "score", broken)
    listing["jobs"].append(J(native_id="9", title="RLHF Evaluator", location="Remote"))
    runner.run(cfg)
    joined = "\n".join(tg.msgs)
    assert "RLHF Evaluator" in joined and "kelime eşleşti" in joined
    assert "puanlaması çalışmadı" in joined


def test_dry_mode_does_not_save(env):
    cfg, listing, tg, calls = env
    runner.run(cfg, mode="dry")
    assert not cfg.state_path.exists()


def test_config_loads_real_files():
    cfg = config.load()
    names = [s["name"] for s in cfg.sources]
    assert "Mercor" in names and "micro1" in names
    assert len(names) == len(set(names))
    for s in cfg.sources:
        assert s["type"] in fetchers.FETCHERS or s["type"] == "auto", s


def test_test_mode_reports_and_keeps_state(env):
    cfg, listing, tg, calls = env
    cfg.sources.append({"name": "Down", "type": "fake", "boom": True})
    runner.run(cfg, mode="test")
    joined = "\n".join(tg.msgs)
    assert "✅ S  1 ilan" in joined
    assert "❌ Down" in joined
    assert "Turkish AI Tutor" in joined
    assert "Test bitti" in joined
    assert not cfg.state_path.exists()


def test_block_places_whole_word():
    assert classify(J(title="AI Trainer", location="Remote - US"), PROFILE, {})[0] == "drop"
    assert classify(J(title="AI Trainer", location="United States; Turkey"), PROFILE, {})[0] == "hot"
    assert classify(J(title="AI Trainer", location="Campus Remote"), PROFILE, {})[0] == "keep"


def test_hot_still_obeys_hard_rules():
    assert classify(J(title="Turkish Medical Translator"), PROFILE, {})[0] == "drop"
    low = J(title="Ads Rater - Turkish", description="approximately $6 per hour")
    assert classify(low, PROFILE, {})[0] == "drop"
    assert classify(J(title="Native Turkish speakers NYC (in-person)", location="New York"), PROFILE, {})[0] == "drop"
