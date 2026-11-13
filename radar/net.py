from __future__ import annotations

import logging
import threading

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)

DEFAULT_TIMEOUT = 25
CONNECT_TIMEOUT = 10


def _t(timeout):
    return (CONNECT_TIMEOUT, timeout)

_local = threading.local()


def _check(r: requests.Response) -> None:
    if r.status_code >= 400:
        body = (r.text or "")[:300].replace("\n", " ")
        raise requests.HTTPError(f"{r.status_code} for {r.url.split('?')[0]} {body}", response=r)


def session() -> requests.Session:
    """One session per thread, with polite retries."""
    s = getattr(_local, "s", None)
    if s is None:
        s = requests.Session()
        retry = Retry(
            total=2,
            backoff_factor=1.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "POST"]),
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        s.headers.update(
            {
                "User-Agent": BROWSER_UA,
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        _local.s = s
    return s


def get(url: str, *, params=None, headers=None, timeout=DEFAULT_TIMEOUT) -> requests.Response:
    r = session().get(url, params=params, headers=headers, timeout=_t(timeout))
    _check(r)
    return r


def get_json(url: str, **kw):
    h = {"Accept": "application/json"}
    h.update(kw.pop("headers", None) or {})
    return get(url, headers=h, **kw).json()


def post_json(url: str, payload, *, headers=None, timeout=DEFAULT_TIMEOUT):
    h = {"Accept": "application/json", "Content-Type": "application/json"}
    h.update(headers or {})
    r = session().post(url, json=payload, headers=h, timeout=_t(timeout))
    _check(r)
    return r.json()


def get_text(url: str, **kw) -> str:
    h = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
    h.update(kw.pop("headers", None) or {})
    r = get(url, headers=h, **kw)
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding or "utf-8"
    return r.text


def post_raw(url: str, payload, *, headers=None, timeout=DEFAULT_TIMEOUT) -> requests.Response:
    """POST without automatic retries. The caller reads status codes itself (used for LLM APIs)."""
    h = {"Accept": "application/json", "Content-Type": "application/json"}
    h.update(headers or {})
    return requests.post(url, json=payload, headers=h, timeout=_t(timeout))
