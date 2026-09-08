"""SubAgent spec: crawls the running app via Playwright MCP to discover
routes dynamically, instead of relying on a hardcoded, app-specific route
list going stale as the fixture grows.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from deepagents import SubAgent

from a11y_fixer import config
from a11y_fixer.adapters.audit_runner import AxeAuditRunner
from a11y_fixer.adapters.mcp_clients import aget_tools

NAME = "audit_crawler"

# `discover_and_audit()` below is the ONLY audit path now - `cli.py` no
# longer branches on whether the target is the bundled Hallucinate.io
# fixture or any other repo, so this fallback must work for any app, not
# just the bundled one. "/" is the one route every web app is guaranteed
# to serve, so it's the only safe app-agnostic fallback when discovery
# finds nothing.
FALLBACK_PAGES: tuple[str, ...] = ("/",)

# A narrow, bounded discovery task (navigate, snapshot, extract hrefs) doesn't
# need, and shouldn't cost, the paid model the rest of the agent uses by default.
DEFAULT_CRAWLER_MODEL = "openrouter:openrouter/free"

# Safety cap on discover_routes()'s own crawl - bounds worst-case time/cost
# against a site with far more pages than the fixture's, without needing a
# CLI flag for what's still a niche tuning knob.
DEFAULT_MAX_PAGES = 20

SYSTEM_PROMPT = """You are the Audit Crawler for The A11y Fixer.

Discover every route in the running app deterministically - do not guess
routes from a visual snapshot. Navigate to the app's root page, then call
`browser_evaluate` to run JavaScript directly against the live page and
extract real routing data, for example:

    Array.from(document.querySelectorAll("a[href]"))
      .map(a => new URL(a.getAttribute("href"), location.href).pathname)

Prefer reading the app's actual client-side router configuration when it's
reachable from the page context (e.g. an Angular `Router`'s registered
paths exposed on `window`, or any router state object already present in
the DOM/JS runtime) over scraping rendered anchors, since rendered links
can miss routes that aren't linked from the current page. Only fall back
to scraping anchor `href`s out of the DOM when no such router data is
reachable via `browser_evaluate`.

Return the discovered routes as relative paths (e.g. "/about"), not full
URLs - the caller joins them with its own base URL, whether that's a local
dev server or a live external site. Deduplicate routes and drop external
links (anything not on the same origin as the page you navigated to). If
the Playwright MCP tools are unavailable, or `browser_evaluate` finds no
routes, report that plainly - the caller falls back to a single "/" page
when discovery comes back empty.
"""


async def build(model: str = DEFAULT_CRAWLER_MODEL) -> SubAgent:
    """Resolve this subagent's MCP tools and return its `SubAgent` spec.

    Kept as an LLM-driven, on-demand subagent the main agent can delegate to
    mid-task (e.g. "did my fix break navigation?") - a genuinely different
    job from `discover_routes()` below, which is the deterministic,
    no-LLM pre-audit crawl.

    A `SubAgent` dict's own `"model"` key overrides the top-level model for
    just this subagent (per `deepagents.graph`'s `spec.get("model", model)`).
    """
    tools = await aget_tools(["playwright"])
    return SubAgent(
        name=NAME,
        description="Crawls the running app via Playwright MCP to discover routes dynamically for the axe-core audit.",
        system_prompt=SYSTEM_PROMPT,
        tools=tools,
        skills=[config.to_virtual_path(config.resolve_skill("playwright-mcp"))],
        model=model,
    )


_EXTRACT_LINKS_JS = "() => Array.from(document.querySelectorAll('a[href]')).map(a => a.href)"

_URL_RE = re.compile(r'https?://[^\s"\'\]\\]+')


def _origin_of(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _path_of(url: str) -> str:
    return urlparse(url).path or "/"


def _extract_urls_from_tool_result(result: object) -> list[str]:
    """`browser_evaluate`'s MCP result shape isn't pinned to one exact
    format across `@playwright/mcp` versions (this project always pulls
    `@latest`) - it may come back as a raw string, a JSON-array-shaped
    string, or wrapped in explanatory text around the JS return value.
    Scan for every http(s) URL substring rather than assuming one exact
    parse, so a harmless format change upstream degrades to "found fewer
    links this pass" instead of an exception.
    """
    text = result if isinstance(result, str) else str(result)
    return _URL_RE.findall(text)


async def discover_routes(base_url: str, *, max_pages: int = DEFAULT_MAX_PAGES) -> list[str]:
    """Deterministic same-origin crawl via the Playwright MCP browser - no
    LLM in the loop, no model call, nothing to hallucinate.

    Breadth-first: navigate to `base_url`, extract every `<a href>` on the
    page via `browser_evaluate`, queue up same-origin links not yet seen,
    and repeat (bounded by `max_pages`) until the queue drains or the cap
    is hit. Returns every path actually visited.

    Replaces the earlier LLM-driven version of this function: that agent
    decided ad hoc whether/how to call `browser_evaluate`, and it silently
    coming back empty (MCP hiccup, model error, or a route genuinely not
    linked from the start page) meant a live `--url` audit could end up
    scanning only the single page it was given, with no visible sign
    discovery had failed (case-12, 2026-09-04: auditing
    https://hallucinate.netlify.app/ this way returned exactly 1 page).

    Never raises: any failure - MCP unavailable, a tool call erroring, an
    unexpected result shape - returns whatever routes were found before the
    failure (or an empty list) so callers can fall back to a known-good
    page list instead of blocking the audit outright.
    """
    routes: set[str] = set()
    try:
        tools = await aget_tools(["playwright"])
        by_name = {tool.name: tool for tool in tools}
        navigate = by_name.get("browser_navigate")
        evaluate = by_name.get("browser_evaluate")
        if navigate is None or evaluate is None:
            print(  # noqa: T201 - CLI output: makes a silent [] fallback visible
                "route discovery: browser_navigate/browser_evaluate not found "
                f"among Playwright MCP tools ({sorted(by_name) or 'none'}) - crawl skipped"
            )
            return []

        origin = _origin_of(base_url)
        queue: list[str] = [base_url]
        print(f"route discovery: crawling {base_url} (max {max_pages} pages)")  # noqa: T201

        while queue and len(routes) < max_pages:
            url = queue.pop(0)
            path = _path_of(url)
            if path in routes:
                continue
            routes.add(path)

            await navigate.ainvoke({"url": url})
            result = await evaluate.ainvoke({"function": _EXTRACT_LINKS_JS})
            new_links = 0
            for href in _extract_urls_from_tool_result(result):
                absolute = urljoin(url, href)
                if _origin_of(absolute) != origin:
                    continue
                if _path_of(absolute) not in routes:
                    queue.append(absolute)
                    new_links += 1
            print(  # noqa: T201 - CLI output: per-page crawl progress
                f"  [{len(routes)}/{max_pages}] visited {path} - "
                f"{new_links} new same-origin link(s) found ({len(queue)} queued)"
            )

        close = by_name.get("browser_close")
        if close is not None:
            await close.ainvoke({})

        hit_cap = bool(queue) and len(routes) >= max_pages
        print(  # noqa: T201 - CLI output: the actual verification line - proves
            # more than "/" was crawled (or explains why not)
            f"route discovery: found {len(routes)} page(s): {sorted(routes)}"
            + (" (stopped early: max_pages reached)" if hit_cap else "")
        )
    except Exception as exc:  # noqa: BLE001 - discovery failing must not block the caller's fallback path
        print(f"route discovery failed ({exc!r}) - falling back")  # noqa: T201

    return sorted(routes)


async def discover_and_audit(runner: AxeAuditRunner) -> dict:
    """Route-aware drop-in replacement for `runner.run()`: start the server,
    discover real routes via this module's deterministic crawler, run one
    combined axe-core scan across all of them, then always stop the server.

    Falls back to `FALLBACK_PAGES` (just "/") if discovery finds nothing - a
    broken or unavailable crawler must never block the audit outright, but
    the fallback must also work on any repo, not just the bundled fixture.
    """
    runner.start_server()
    try:
        # http:// is correct here: `ng serve` is a local dev server with no TLS.
        base_url = f"http://{runner.host}:{runner.port}"  # noqa: S310
        routes = await discover_routes(base_url)
        if not routes:
            print(  # noqa: T201
                "route discovery found nothing - falling back to "
                f"{FALLBACK_PAGES} (audit may be incomplete for this repo)"
            )
        return runner.audit_pages(pages=tuple(routes) if routes else FALLBACK_PAGES)
    finally:
        runner.stop_server()
