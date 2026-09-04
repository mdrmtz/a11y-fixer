"""SubAgent spec: crawls the running app via Playwright MCP to discover
routes dynamically, instead of relying on a hardcoded, app-specific route
list going stale as the fixture grows.
"""

from __future__ import annotations

from deepagents import SubAgent
from langchain.agents.structured_output import ToolStrategy
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel

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


class DiscoveredRoutes(BaseModel):
    """Structured output for the standalone discovery agent below - replaces
    the free-form "return the discovered route list" prose with a real,
    validated data structure.
    """

    routes: list[str]


async def discover_routes(
    base_url: str, *, model: str = DEFAULT_CRAWLER_MODEL
) -> list[str]:
    """Run this module's own prompt/tools as a standalone single-agent
    graph and return the routes it discovers - no subagent delegation is
    needed for a one-shot discovery task.

    Never raises: any failure (MCP unavailable, model error, malformed
    output) returns an empty list so callers can fall back to a known-good
    page list instead of blocking the audit outright.
    """
    from deepagents import (
        create_deep_agent,
    )  # noqa: PLC0415 - deferred: keeps module import side-effect-free for tests

    config.configure_model_providers()
    try:
        tools = await aget_tools(["playwright"])
        graph = create_deep_agent(
            model=model,
            tools=tools,
            system_prompt=SYSTEM_PROMPT,
            skills=[config.to_virtual_path(config.resolve_skill("playwright-mcp"))],
            response_format=ToolStrategy(schema=DiscoveredRoutes),
            checkpointer=InMemorySaver(),
        )
        result = await graph.ainvoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": f"Discover every route in the running app at {base_url}.",
                    }
                ]
            },
            config={
                "configurable": {
                    "thread_id": "audit-crawler-discovery",
                    "recursion_limit": 30,
                }
            },
        )
        response = result.get("structured_response")
        return list(response.routes) if response else []
    except (
        Exception
    ):  # noqa: BLE001 - discovery failing must not block the caller's fallback path
        return []


async def discover_and_audit(
    runner: AxeAuditRunner, *, model: str = DEFAULT_CRAWLER_MODEL
) -> dict:
    """Route-aware drop-in replacement for `runner.run()`: start the server,
    discover real routes via this module's own crawler prompt, run one
    combined axe-core scan across all of them, then always stop the server.

    Falls back to `FALLBACK_PAGES` (just "/") if discovery finds nothing - a
    broken or unavailable crawler must never block the audit outright, but
    the fallback must also work on any repo, not just the bundled fixture.
    """
    runner.start_server()
    try:
        # http:// is correct here: `ng serve` is a local dev server with no TLS.
        base_url = f"http://{runner.host}:{runner.port}"  # noqa: S310
        routes = await discover_routes(base_url, model=model)
        if not routes:
            print(  # noqa: T201
                "route discovery found nothing - falling back to "
                f"{FALLBACK_PAGES} (audit may be incomplete for this repo)"
            )
        return runner.audit_pages(pages=tuple(routes) if routes else FALLBACK_PAGES)
    finally:
        runner.stop_server()
