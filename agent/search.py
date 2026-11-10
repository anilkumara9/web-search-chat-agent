"""The agent's single tool: web search.

Backends (first configured wins): Tavily (TAVILY_API_KEY), Brave (BRAVE_API_KEY),
otherwise DuckDuckGo's keyless HTML endpoint. Results are cached briefly so a
repeated or follow-up query costs no network time.
"""
import html
import json
import os
import re
import time
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
_cache: dict = {}
CACHE_TTL = 300


def _http(url, data=None, headers=None, timeout=8):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def highest_versions(text: str, product: str, n: int = 3):
    """Computed in code from a page's full text: the highest x.y.z strings written right after
    the product name (e.g. "Python 3.15.2", "go1.27.3"). Pre-release markers (a1, rc1, alpha...) are skipped. A hint only: it is not a full release list, the model still judges."""
    pat = re.compile(r"(?<![A-Za-z0-9])" + re.escape(product) + r"[ -]?v?(\d{1,2}\.\d{1,3}\.\d{1,3})(?![\d.]|a\d|b\d|rc\d|[ -]?(?:alpha|beta|rc|dev|pre))", re.I)
    vs = {tuple(map(int, m.split("."))) for m in pat.findall(text)}
    return [".".join(map(str, v)) for v in sorted(vs, reverse=True)[:n]]


def _tavily(q, n, site=None, raw=False):
    payload = {"api_key": os.environ["TAVILY_API_KEY"], "query": q,
               "max_results": n, "search_depth": os.environ.get("TAVILY_DEPTH", "advanced"),
               "chunks_per_source": 2}
    if site:
        payload["include_domains"] = [site]
    if raw:
        payload["include_raw_content"] = "text"
    body = json.dumps(payload).encode()
    d = json.loads(_http("https://api.tavily.com/search", body,
                         {"Content-Type": "application/json"}))
    out = []
    for r in d.get("results", []):
        item = {"title": r["title"], "url": r["url"], "snippet": r.get("content", "")[:400]}
        if r.get("published_date"):
            item["published"] = r["published_date"]
        if raw and r.get("raw_content"):
            item["version_hints"] = highest_versions(r["raw_content"], (site or "").split(".")[0])
        out.append(item)
    return out


def _brave(q, n, site=None):
    q = f"site:{site} {q}" if site else q
    url = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode({"q": q, "count": n})
    d = json.loads(_http(url, headers={"X-Subscription-Token": os.environ["BRAVE_API_KEY"],
                                       "Accept": "application/json"}))
    return [{"title": r["title"], "url": r["url"], "snippet": re.sub("<[^>]+>", "", r.get("description", ""))}
            for r in d.get("web", {}).get("results", [])[:n]]


_DDG_RESULT = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>.*?'
    r'class="result__snippet"[^>]*>(?P<snip>.*?)</a>', re.S)


def parse_ddg(page: str, n: int):
    out = []
    for m in _DDG_RESULT.finditer(page):
        href = html.unescape(m["href"])
        if "uddg=" in href:
            href = urllib.parse.unquote(urllib.parse.parse_qs(urllib.parse.urlparse(href).query)["uddg"][0])
        clean = lambda s: html.unescape(re.sub("<[^>]+>", "", s)).strip()
        out.append({"title": clean(m["title"]), "url": href, "snippet": clean(m["snip"])})
        if len(out) >= n:
            break
    return out


def _ddg(q, n, site=None):
    q = f"site:{site} {q}" if site else q
    page = _http("https://html.duckduckgo.com/html/", urllib.parse.urlencode({"q": q}).encode(),
                 {"Content-Type": "application/x-www-form-urlencoded"})
    return parse_ddg(page, n)


def web_search(query: str, site: str | None = None, max_results: int = 4, raw: bool = False) -> dict:
    site = (site or "").strip().lower().removeprefix("https://").removeprefix("http://").strip("/") or None
    key = (query.strip().lower(), site, max_results, raw)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        age = int(time.time() - hit[0])
        return {**hit[1], "cached": True, "cached_age_s": age,
                "note": f"CACHED result from {age}s ago, not a fresh lookup"}
    backend = ("tavily" if os.environ.get("TAVILY_API_KEY") else
               "brave" if os.environ.get("BRAVE_API_KEY") else "duckduckgo")
    t0 = time.time()
    try:
        results = {"tavily": _tavily, "brave": _brave, "duckduckgo": _ddg}[backend](query, max_results, site, **({"raw": True} if raw and backend == "tavily" else {}))
        out = {"query": query, "site": site, "backend": backend, "results": results,
               "elapsed_ms": int((time.time() - t0) * 1000)}
        if not results:
            # DuckDuckGo serves a bot challenge to some IPs; never cache or hide that.
            return {"query": query, "backend": backend, "results": [],
                    "error": "no results returned (provider may have blocked the request); "
                             "tell the user search failed instead of guessing"}
    except Exception as e:  # network/provider failure must be visible to the model
        return {"query": query, "backend": backend, "results": [], "error": f"{type(e).__name__}: {e}"}
    _cache[key] = (time.time(), out)
    return out


TOOL_SPEC = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the live web. Use for anything recent, factual, numeric or "
                       "that you are not certain about. Returns titles, URLs and snippets.",
        "parameters": {"type": "object",
                       "properties": {"query": {"type": "string", "description": "Specific search query"},
                                      "site": {"type": "string", "description": "Optional domain to restrict results to, e.g. python.org. Use the official site for 'latest version/release' questions."}},
                       "required": ["query"]},
    },
}
