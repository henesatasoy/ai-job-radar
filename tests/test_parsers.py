from radar import fetchers
from radar.fetchers import (
    _sitemap_entries,
    extract_links,
    page_meta,
    parse_ashby,
    parse_greenhouse,
    parse_html_links,
    parse_lever,
    parse_recruitee,
    parse_rippling,
    parse_sitemap_urls,
    parse_workable_v3,
    parse_workable_widget,
    parse_workday,
)

SRC = lambda **kw: dict({"name": "X", "slug": "x"}, **kw)


def test_greenhouse():
    data = {
        "jobs": [
            {
                "id": 123,
                "title": "AI Content Specialist &amp; Writer",
                "absolute_url": "https://job-boards.greenhouse.io/x/jobs/123",
                "location": {"name": "Remote - EMEA"},
                "first_published": "2026-11-12T10:00:00-04:00",
                "content": "&lt;p&gt;Pay $30-45/hr. Turkish a plus&lt;/p&gt;",
            }
        ]
    }
    [j] = parse_greenhouse(data, SRC())
    assert j.native_id == "123"
    assert j.title == "AI Content Specialist & Writer"
    assert j.remote is True
    assert j.posted_at.year == 2026
    assert "Pay $30-45/hr" in j.description and "<p>" not in j.description


def test_lever():
    data = [
        {
            "id": "abc",
            "text": "Ads Quality Rater - Turkish (Turkey)",
            "hostedUrl": "https://jobs.lever.co/w/abc",
            "createdAt": 1763000000000,
            "categories": {"location": "Turkey", "allLocations": ["Turkey", "Turkey"], "commitment": "Part-time"},
            "workplaceType": "remote",
            "descriptionPlain": "Rate ads.",
            "lists": [{"text": "Requirements", "content": "<li>Native Turkish</li>"}],
            "additionalPlain": "Pay Rate approximately $6 per hour",
        }
    ]
    [j] = parse_lever(data, SRC())
    assert j.location.startswith("Turkey") and "remote" in j.location
    assert j.remote is True
    assert "Native Turkish" in j.description
    assert j.posted_at is not None


def test_ashby_skips_unlisted():
    data = {
        "jobs": [
            {"id": "1", "title": "A", "jobUrl": "u1", "location": "Remote", "isRemote": True, "publishedAt": "2026-11-01T00:00:00Z"},
            {"id": "2", "title": "B", "jobUrl": "u2", "isListed": False},
        ]
    }
    jobs = parse_ashby(data, SRC())
    assert [j.native_id for j in jobs] == ["1"]


def test_workable_both_shapes():
    w = {"jobs": [{"shortcode": "7F43", "title": "T", "telecommuting": True, "country": "Turkey", "published_on": "2026-11-10"}]}
    [j] = parse_workable_widget(w, SRC(slug="toloka-ai"))
    assert j.native_id == "7F43" and j.remote and "Turkey" in j.location
    v3 = [{"shortcode": "AB12", "title": "T2", "remote": False, "location": {"city": "NYC", "country": "United States"}}]
    [k] = parse_workable_v3(v3, SRC(slug="toloka-ai"))
    assert k.url == "https://apply.workable.com/toloka-ai/j/AB12/"


def test_recruitee_rippling_workday():
    [r] = parse_recruitee({"offers": [{"id": 9, "slug": "s", "title": "Linguist", "remote": True, "location": "Istanbul"}]}, SRC(slug="dataforce"))
    assert r.url.endswith("/o/s") and "remote" in r.location
    rip = parse_rippling(
        [
            {"uuid": "u1", "name": "Transcriber", "url": "https://ats.rippling.com/perle/jobs/u1", "workLocation": {"label": "Brazil"}},
            {"uuid": "u1", "name": "Transcriber", "url": "https://ats.rippling.com/perle/jobs/u1", "workLocation": {"label": "Turkey"}},
        ],
        SRC(slug="perle"),
    )
    assert len(rip) == 1 and "Turkey" in rip[0].location
    wd = parse_workday(
        [{"title": "Rater", "externalPath": "/job/Remote/Rater_R1", "locationsText": "Remote", "postedOn": "Posted Today"}],
        SRC(host="c.wd1.myworkdayjobs.com", site="Global"),
    )
    assert wd[0].url == "https://c.wd1.myworkdayjobs.com/Global/job/Remote/Rater_R1"


def test_sitemap():
    xml = """<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://work.mercor.com/arena</loc></url>
      <url><loc>https://work.mercor.com/jobs/list_AAABm1/turkish-ai-tutor</loc><lastmod>2026-11-12</lastmod></url>
      <url><loc>https://work.mercor.com/jobs/apply/interview-scheduled</loc></url>
    </urlset>"""
    children, urls = _sitemap_entries(xml)
    assert children == [] and len(urls) == 3
    jobs = parse_sitemap_urls(urls, {"name": "Mercor", "include": r"/jobs/list_[^/]+/[^/?#]+"})
    assert len(jobs) == 1
    assert jobs[0].title == "Turkish ai tutor"
    idx = """<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://a/s1.xml</loc></sitemap></sitemapindex>"""
    assert _sitemap_entries(idx)[0] == ["https://a/s1.xml"]


def test_html_links_and_id_regex():
    html = """
    <a href="/jobs/turkish-annotator--a1b2/">Turkish Annotator <span>$20/hr</span></a>
    <a href="/jobs/turkish-annotator--a1b2/">Apply</a>
    <a href="/jobs/language/tr/">Language</a>
    <a href="https://www.opentrain.ai/jobs/other--zz9/#x">Other</a>
    """
    src = {"name": "OpenTrain", "link": r"/jobs/[^/]+--[^/]+/?$", "id_regex": r"--([^/?#]+)/?$"}
    jobs = parse_html_links(html, "https://www.opentrain.ai/jobs/country/turkiye/", src)
    ids = sorted(j.native_id for j in jobs)
    assert ids == ["a1b2", "zz9"]
    t = [j for j in jobs if j.native_id == "a1b2"][0]
    assert t.title.startswith("Turkish Annotator")


def test_html_paging_stops_when_nothing_new(monkeypatch):
    pages = {
        "https://a.ai/jobs/": '<a href="/jobs/11111111-1111-1111-1111-111111111111/">One</a>',
        "https://a.ai/jobs/page/2/": '<a href="/jobs/22222222-2222-2222-2222-222222222222/">Two</a>',
        "https://a.ai/jobs/page/3/": '<a href="/jobs/22222222-2222-2222-2222-222222222222/">Two</a>',
    }
    calls = []

    def fake(url, **kw):
        calls.append(url)
        if url not in pages:
            raise RuntimeError("404")
        return pages[url]

    monkeypatch.setattr(fetchers.net, "get_text", fake)
    src = {"name": "A", "url": "https://a.ai/jobs/", "page_url": "https://a.ai/jobs/page/{n}/", "pages": 10, "link": r"/jobs/[0-9a-f-]{36}"}
    jobs = fetchers.html_links(src)
    assert len(jobs) == 2
    assert calls[-1] == "https://a.ai/jobs/page/3/"


def test_page_meta():
    html = '<html><head><title>x</title><meta property="og:title" content="Turkish Expert | micro1"><meta name="description" content="Skills a, b"></head></html>'
    m = page_meta(html)
    assert m["og:title"] == "Turkish Expert | micro1"
    assert m["description"] == "Skills a, b"


def test_probe(monkeypatch):
    calls = []

    def fake_gh(src):
        calls.append(("greenhouse", src["slug"]))
        raise RuntimeError("404")

    def fake_lever(src):
        calls.append(("lever", src["slug"]))
        if src["slug"] == "b":
            from radar.models import Job

            return [Job(source=src["name"], native_id="1", title="t", url="u")]
        return []

    monkeypatch.setitem(fetchers.FETCHERS, "greenhouse", fake_gh)
    monkeypatch.setitem(fetchers.FETCHERS, "lever", fake_lever)
    res = fetchers.probe({"name": "P", "candidates": ["a", "b"], "try_types": ["greenhouse", "lever"]})
    assert res[0] == "lever" and res[1] == "b"


def test_greenhouse_falls_back_to_html(monkeypatch):
    def api_404(url, **kw):
        raise RuntimeError("404")

    page = '<a href="https://job-boards.eu.greenhouse.io/agency/jobs/4738026101">Turkish Writer Remote</a>'
    monkeypatch.setattr(fetchers.net, "get_json", api_404)
    monkeypatch.setattr(fetchers.net, "get_text", lambda url, **kw: page)
    jobs = fetchers.greenhouse({"name": "Invisible", "slug": "agency", "eu": True})
    assert [j.native_id for j in jobs] == ["4738026101"]
    # probing must not fall back
    import pytest
    with pytest.raises(RuntimeError):
        fetchers.greenhouse({"name": "P", "slug": "x", "no_fallback": True})
