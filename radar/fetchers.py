"""One fetch function per source type.

Each function takes the source dict from sources.yaml and returns a list of Job.
They raise on network or parse errors. The runner catches and records them.
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import urljoin, urlparse

from . import net
from .models import Job
from .textutil import clean, parse_dt, slug_to_title, strip_html

log = logging.getLogger(__name__)

DESC_LIMIT = 3000

FETCHERS: dict[str, Callable[[dict], list[Job]]] = {}


def fetcher(name: str):
    def deco(fn):
        FETCHERS[name] = fn
        return fn

    return deco


def _company(src: dict) -> str:
    return src.get("company") or src["name"]


# ---------------------------------------------------------------- Greenhouse

@fetcher("greenhouse")
def greenhouse(src: dict) -> list[Job]:
    host = "boards-api.eu.greenhouse.io" if src.get("eu") else "boards-api.greenhouse.io"
    data = net.get_json(f"https://{host}/v1/boards/{src['slug']}/jobs", params={"content": "true"})
    return parse_greenhouse(data, src)


def parse_greenhouse(data: dict, src: dict) -> list[Job]:
    out = []
    for j in data.get("jobs", []):
        loc = clean((j.get("location") or {}).get("name"))
        out.append(
            Job(
                source=src["name"],
                native_id=str(j["id"]),
                title=clean(j.get("title")),
                url=j.get("absolute_url") or "",
                company=_company(src),
                location=loc,
                remote=("remote" in loc.lower()) or None,
                posted_at=parse_dt(j.get("first_published") or j.get("updated_at")),
                description=strip_html(j.get("content"), DESC_LIMIT),
            )
        )
    return out


# ---------------------------------------------------------------- Lever

@fetcher("lever")
def lever(src: dict) -> list[Job]:
    host = "api.eu.lever.co" if src.get("eu") else "api.lever.co"
    data = net.get_json(f"https://{host}/v0/postings/{src['slug']}", params={"mode": "json"}, timeout=60)
    return parse_lever(data, src)


def parse_lever(data: list, src: dict) -> list[Job]:
    out = []
    for p in data or []:
        cats = p.get("categories") or {}
        locs = cats.get("allLocations") or ([cats["location"]] if cats.get("location") else [])
        loc = ", ".join(dict.fromkeys(clean(x) for x in locs if x))
        wp = (p.get("workplaceType") or "").lower()
        parts = [p.get("descriptionPlain") or ""]
        for lst in p.get("lists") or []:
            parts.append(clean(lst.get("text")))
            parts.append(strip_html(lst.get("content")))
        parts.append(p.get("additionalPlain") or "")
        sal = p.get("salaryRange") or {}
        pay = ""
        if sal.get("min") and str(sal.get("interval", "")).lower().startswith("per-hour"):
            pay = f"{sal.get('currency', '')} {sal['min']}-{sal.get('max', sal['min'])}/hour"
        out.append(
            Job(
                source=src["name"],
                native_id=str(p["id"]),
                title=clean(p.get("text")),
                url=p.get("hostedUrl") or "",
                company=_company(src),
                location=loc + (f" ({wp})" if wp and wp not in loc.lower() else ""),
                remote=(wp == "remote") or ("remote" in loc.lower()) or None,
                posted_at=parse_dt(p.get("createdAt")),
                description=strip_html("\n".join(x for x in parts if x), DESC_LIMIT),
                pay=pay,
                extra={"commitment": cats.get("commitment", "")},
            )
        )
    return out


# ---------------------------------------------------------------- Ashby

@fetcher("ashby")
def ashby(src: dict) -> list[Job]:
    data = net.get_json(
        f"https://api.ashbyhq.com/posting-api/job-board/{src['slug']}",
        params={"includeCompensation": "true"},
        timeout=60,
    )
    return parse_ashby(data, src)


def parse_ashby(data: dict, src: dict) -> list[Job]:
    out = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location") or ""]
        for s in j.get("secondaryLocations") or []:
            locs.append(s.get("location") if isinstance(s, dict) else str(s))
        loc = ", ".join(dict.fromkeys(clean(x) for x in locs if x))
        comp = j.get("compensation") or {}
        out.append(
            Job(
                source=src["name"],
                native_id=str(j["id"]),
                title=clean(j.get("title")),
                url=j.get("jobUrl") or "",
                company=_company(src),
                location=loc,
                remote=bool(j.get("isRemote")) or None,
                posted_at=parse_dt(j.get("publishedAt")),
                description=strip_html(j.get("descriptionPlain") or j.get("descriptionHtml"), DESC_LIMIT),
                pay=clean(comp.get("compensationTierSummary") or comp.get("scrapeableCompensationSalarySummary")),
            )
        )
    return out


# ---------------------------------------------------------------- Workable

@fetcher("workable")
def workable(src: dict) -> list[Job]:
    slug = src["slug"]
    try:
        data = net.get_json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}", params={"details": "true"})
        jobs = parse_workable_widget(data, src)
        if jobs:
            return jobs
    except Exception as e:  # fall back to the v3 endpoint
        log.info("workable widget failed for %s: %s", slug, e)
    return _workable_v3(src)


def parse_workable_widget(data: dict, src: dict) -> list[Job]:
    out = []
    for j in data.get("jobs", []):
        code = j.get("shortcode") or j.get("code") or j.get("id")
        locs = []
        for l in j.get("locations") or []:
            locs.append(", ".join(x for x in (l.get("city"), l.get("region"), l.get("country")) if x))
        if not locs:
            locs.append(", ".join(x for x in (j.get("city"), j.get("state"), j.get("country")) if x))
        remote = bool(j.get("telecommuting")) or None
        loc = "; ".join(x for x in locs if x) + (" (remote)" if remote else "")
        out.append(
            Job(
                source=src["name"],
                native_id=str(code),
                title=clean(j.get("title")),
                url=j.get("shortlink") or j.get("url") or f"https://apply.workable.com/{src['slug']}/j/{code}/",
                company=_company(src),
                location=loc.strip(),
                remote=remote,
                posted_at=parse_dt(j.get("published_on") or j.get("created_at")),
                description=strip_html(j.get("description"), DESC_LIMIT),
            )
        )
    return out


def _workable_v3(src: dict) -> list[Job]:
    slug = src["slug"]
    url = f"https://apply.workable.com/api/v3/accounts/{slug}/jobs"
    body = {"query": "", "location": [], "department": [], "worktype": [], "remote": []}
    results, token = [], None
    for _ in range(20):
        payload = dict(body, token=token) if token else body
        data = net.post_json(url, payload)
        results.extend(data.get("results", []))
        token = data.get("nextPage")
        if not token:
            break
    return parse_workable_v3(results, src)


def parse_workable_v3(results: list, src: dict) -> list[Job]:
    out = []
    for j in results:
        code = j.get("shortcode") or j.get("id")
        l = j.get("location") or {}
        loc = ", ".join(x for x in (l.get("city"), l.get("region"), l.get("country")) if x)
        remote = bool(j.get("remote")) or None
        out.append(
            Job(
                source=src["name"],
                native_id=str(code),
                title=clean(j.get("title")),
                url=f"https://apply.workable.com/{src['slug']}/j/{code}/",
                company=_company(src),
                location=loc + (" (remote)" if remote else ""),
                remote=remote,
                posted_at=parse_dt(j.get("published")),
            )
        )
    return out


# ---------------------------------------------------------------- Recruitee

@fetcher("recruitee")
def recruitee(src: dict) -> list[Job]:
    data = net.get_json(f"https://{src['slug']}.recruitee.com/api/offers/")
    return parse_recruitee(data, src)


def parse_recruitee(data: dict, src: dict) -> list[Job]:
    out = []
    for o in data.get("offers", []):
        loc = clean(o.get("location")) or ", ".join(x for x in (o.get("city"), o.get("country")) if x)
        remote = bool(o.get("remote")) or None
        out.append(
            Job(
                source=src["name"],
                native_id=str(o.get("id") or o.get("slug")),
                title=clean(o.get("title")),
                url=o.get("careers_url") or f"https://{src['slug']}.recruitee.com/o/{o.get('slug')}",
                company=_company(src),
                location=loc + (" (remote)" if remote and "remote" not in loc.lower() else ""),
                remote=remote,
                posted_at=parse_dt(o.get("published_at") or o.get("created_at")),
                description=strip_html((o.get("description") or "") + "\n" + (o.get("requirements") or ""), DESC_LIMIT),
            )
        )
    return out


# ---------------------------------------------------------------- Rippling

@fetcher("rippling")
def rippling(src: dict) -> list[Job]:
    data = net.get_json(f"https://api.rippling.com/platform/api/ats/v1/board/{src['slug']}/jobs")
    return parse_rippling(data, src)


def parse_rippling(data: list, src: dict) -> list[Job]:
    by_id: dict[str, Job] = {}
    for j in data or []:
        uid = str(j.get("uuid") or j.get("id"))
        loc = clean((j.get("workLocation") or {}).get("label"))
        if uid in by_id:
            if loc and loc not in by_id[uid].location:
                by_id[uid].location += f"; {loc}"
            continue
        by_id[uid] = Job(
            source=src["name"],
            native_id=uid,
            title=clean(j.get("name")),
            url=j.get("url") or f"https://ats.rippling.com/{src['slug']}/jobs/{uid}",
            company=_company(src),
            location=loc,
            remote=("remote" in loc.lower()) or None,
        )
    return list(by_id.values())


# ---------------------------------------------------------------- Workday

@fetcher("workday")
def workday(src: dict) -> list[Job]:
    host, tenant, site = src["host"], src["tenant"], src["site"]
    url = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    postings, offset, total = [], 0, None
    while True:
        data = net.post_json(url, {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": ""})
        page = data.get("jobPostings") or []
        postings.extend(page)
        if total is None:
            total = data.get("total") or 0
        offset += 20
        if not page or offset >= min(total, src.get("max_jobs", 300)):
            break
    return parse_workday(postings, src)


def parse_workday(postings: list, src: dict) -> list[Job]:
    out = []
    for p in postings:
        path = p.get("externalPath") or ""
        if not path:
            continue
        out.append(
            Job(
                source=src["name"],
                native_id=path,
                title=clean(p.get("title")),
                url=f"https://{src['host']}/{src['site']}{path}",
                company=_company(src),
                location=clean(p.get("locationsText")),
                remote=("remote" in (p.get("locationsText") or "").lower()) or None,
                extra={"posted_on": p.get("postedOn", "")},
            )
        )
    return out


# ---------------------------------------------------------------- Sitemap

_SM_NS = re.compile(r"^\{[^}]+\}")


def _sitemap_entries(xml_text: str) -> tuple[list[str], list[tuple[str, str]]]:
    """Returns (child sitemap urls, [(loc, lastmod)])."""
    root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    tag = _SM_NS.sub("", root.tag)
    children, urls = [], []
    for el in root:
        loc = lastmod = ""
        for sub in el:
            name = _SM_NS.sub("", sub.tag)
            if name == "loc":
                loc = (sub.text or "").strip()
            elif name == "lastmod":
                lastmod = (sub.text or "").strip()
        if not loc:
            continue
        if tag == "sitemapindex":
            children.append(loc)
        else:
            urls.append((loc, lastmod))
    return children, urls


@fetcher("sitemap")
def sitemap(src: dict) -> list[Job]:
    text = net.get_text(src["url"], timeout=40)
    children, urls = _sitemap_entries(text)
    for child in children[: src.get("max_children", 10)]:
        if src.get("child_include") and not re.search(src["child_include"], child):
            continue
        _, more = _sitemap_entries(net.get_text(child, timeout=40))
        urls.extend(more)
    return parse_sitemap_urls(urls, src)


def parse_sitemap_urls(urls: list[tuple[str, str]], src: dict) -> list[Job]:
    pat = re.compile(src["include"])
    out, seen = [], set()
    for loc, lastmod in urls:
        m = pat.search(loc)
        if not m:
            continue
        path = urlparse(loc).path.rstrip("/")
        if path in seen:
            continue
        seen.add(path)
        last = path.rsplit("/", 1)[-1]
        title = slug_to_title(last) if src.get("title_from", "slug") == "slug" else ""
        out.append(
            Job(
                source=src["name"],
                native_id=path,
                title=title or "(başlık sayfadan alınacak)",
                url=loc,
                company=_company(src),
                extra={"lastmod": lastmod},
            )
        )
    return out


# ---------------------------------------------------------------- Plain HTML link lists


class _AnchorParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._stack: list[list] = []  # [href, text parts]

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            self._stack.append([href, []])

    def handle_endtag(self, tag):
        if tag == "a" and self._stack:
            href, parts = self._stack.pop()
            if href:
                self.links.append((href, " ".join(parts)))
            if self._stack:  # nested anchors, keep text for parent too
                self._stack[-1][1].extend(parts)

    def handle_data(self, data):
        if self._stack:
            self._stack[-1][1].append(data)


def extract_links(html_text: str, base_url: str, pattern: str) -> list[tuple[str, str]]:
    p = _AnchorParser()
    p.feed(html_text)
    rx = re.compile(pattern)
    out: dict[str, str] = {}
    for href, text in p.links:
        canon = urljoin(base_url, href.strip()).split("#", 1)[0]
        if not rx.search(canon):
            continue
        t = clean(text)
        # keep the longest anchor text seen for the same link
        if canon not in out or len(t) > len(out[canon]):
            out[canon] = t
    return list(out.items())


def _page_urls(src: dict) -> list[list[str]]:
    """Groups of page urls. Each group is paged until a page adds nothing new."""
    groups = []
    pages = int(src.get("pages", 1))
    for url in src.get("urls") or [src["url"]]:
        group = [url]
        if pages > 1:
            if src.get("page_url"):
                group += [src["page_url"].format(n=i) for i in range(2, pages + 1)]
            else:
                param = src.get("page_param", "page")
                sep = "&" if "?" in url else "?"
                group += [f"{url}{sep}{param}={i}" for i in range(2, pages + 1)]
        groups.append(group)
    return groups


@fetcher("html")
def html_links(src: dict) -> list[Job]:
    jobs: dict[str, Job] = {}
    for gi, group in enumerate(_page_urls(src)):
        for pi, page_url in enumerate(group):
            try:
                text = net.get_text(page_url, timeout=40)
            except Exception:
                if gi == 0 and pi == 0:
                    raise  # the main page must work
                break  # a missing later page just ends paging
            before = len(jobs)
            for j in parse_html_links(text, page_url, src):
                jobs.setdefault(j.native_id, j)
            if len(jobs) == before:
                break
    return list(jobs.values())


def parse_html_links(text: str, page_url: str, src: dict) -> list[Job]:
    out = []
    id_rx = re.compile(src["id_regex"]) if src.get("id_regex") else None
    for url, anchor in extract_links(text, page_url, src["link"]):
        path = urlparse(url).path.rstrip("/")
        nid = path
        if id_rx:
            m = id_rx.search(url)
            if m:
                nid = m.group(1) if m.groups() else m.group(0)
        title = anchor[:220] if anchor else slug_to_title(path.rsplit("/", 1)[-1])
        out.append(Job(source=src["name"], native_id=nid, title=title, url=url, company=_company(src)))
    return out


# ---------------------------------------------------------------- Page enrichment

_META_RE = re.compile(
    r"<meta[^>]+(?:property|name)\s*=\s*[\"'](og:title|og:description|description|twitter:title)[\"'][^>]*>",
    re.I,
)
_CONTENT_RE = re.compile(r"content\s*=\s*[\"']([^\"']*)[\"']", re.I)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


def page_meta(html_text: str) -> dict:
    meta = {}
    for m in _META_RE.finditer(html_text):
        c = _CONTENT_RE.search(m.group(0))
        if c:
            meta.setdefault(m.group(1).lower(), clean(c.group(1)))
    t = _TITLE_RE.search(html_text)
    if t:
        meta.setdefault("title", clean(t.group(1)))
    return meta


def enrich(job: Job) -> None:
    """Fetch the job page to get a better title and some text for scoring."""
    text = net.get_text(job.url, timeout=30)
    meta = page_meta(text)
    title = meta.get("og:title") or meta.get("twitter:title") or meta.get("title")
    if title and (job.title.startswith("(") or len(job.title) < 4 or job.extra.get("prefer_page_title")):
        job.title = title
    desc = meta.get("og:description") or meta.get("description") or ""
    body = strip_html(text, DESC_LIMIT)
    job.description = (desc + "\n" + body).strip()[:DESC_LIMIT]


# ---------------------------------------------------------------- Auto discovery


AUTO_TYPES = ("greenhouse", "lever", "ashby", "workable", "recruitee")


def probe(src: dict) -> tuple[str, str, list[Job]] | None:
    """Try candidate slugs on the common ATS APIs. Returns the first that has jobs."""
    types = src.get("try_types") or AUTO_TYPES
    for slug in src.get("candidates", []):
        for t in types:
            trial = dict(src, type=t, slug=slug)
            try:
                jobs = FETCHERS[t](trial)
            except Exception:
                continue
            if jobs:
                return t, slug, jobs
    return None
