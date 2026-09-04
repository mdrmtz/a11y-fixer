from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from a11y_fixer import config
from a11y_fixer.agents import audit_crawler, codebase_compiler, compliance_planner, qa_critic
from a11y_fixer.domain.rubric import RubricComponents, score_candidate


@pytest.fixture(autouse=True)
def _fake_tools(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    fake = AsyncMock(return_value=["fake-tool"])
    for module in (compliance_planner, codebase_compiler, qa_critic, audit_crawler):
        monkeypatch.setattr(module, "aget_tools", fake)
    return fake


async def test_compliance_planner_spec() -> None:
    spec = await compliance_planner.build()
    assert spec["name"] == "compliance_planner"
    assert spec["tools"] == ["fake-tool"]
    skill_dir = config.skills_dir() / "a11y-fixer"
    assert spec["skills"] == [config.to_virtual_path(skill_dir)]
    assert skill_dir.is_dir()


async def test_qa_critic_spec() -> None:
    spec = await qa_critic.build()
    assert spec["name"] == "qa_critic"
    assert "skills" not in spec


async def test_qa_critic_spec_includes_score_rubric_tool() -> None:
    spec = await qa_critic.build()
    assert qa_critic.score_rubric in spec["tools"]
    assert "fake-tool" in spec["tools"]  # the chrome-devtools MCP tools are still present too


def test_score_rubric_matches_domain_rubric_computation() -> None:
    components = RubricComponents(build_pass=True, ast_valid=True, wcag_judge_score=0.8, cls=0.02, bbox_drift_pct=1.0)
    expected = score_candidate(components)

    result = qa_critic.score_rubric.invoke(
        {"build_pass": True, "ast_valid": True, "wcag_judge_score": 0.8, "cls": 0.02, "bbox_drift_pct": 1.0}
    )

    assert result["total"] == expected.total
    assert result["components"] == expected.components


def test_score_rubric_defaults_visual_stability_to_unmeasured() -> None:
    result = qa_critic.score_rubric.invoke({"build_pass": True, "ast_valid": True, "wcag_judge_score": 0.5})

    assert result["visual_stability_measured"] is False
    assert result["components"]["visual_stability"] == 0.0


def test_score_rubric_rejects_out_of_range_wcag_score() -> None:
    with pytest.raises(ValueError, match="wcag_judge_score"):
        qa_critic.score_rubric.invoke({"build_pass": True, "ast_valid": True, "wcag_judge_score": 1.5})


async def test_audit_crawler_spec() -> None:
    spec = await audit_crawler.build()
    assert spec["name"] == "audit_crawler"
    skill_dir = config.skills_dir() / "playwright-mcp"
    assert spec["skills"] == [config.to_virtual_path(skill_dir)]
    assert skill_dir.is_dir()


async def test_audit_crawler_spec_defaults_to_free_tier_openrouter() -> None:
    spec = await audit_crawler.build()
    assert spec["model"] == "openrouter:openrouter/free"


async def test_audit_crawler_spec_model_is_overridable() -> None:
    spec = await audit_crawler.build(model="ollama:llama3.1")
    assert spec["model"] == "ollama:llama3.1"


class _FakeDiscoveryGraph:
    def __init__(self, structured_response: object) -> None:
        self._structured_response = structured_response

    async def ainvoke(self, *_args: object, **_kwargs: object) -> dict:
        return {"structured_response": self._structured_response}


async def test_discover_routes_returns_the_discovered_list(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_graph = _FakeDiscoveryGraph(audit_crawler.DiscoveredRoutes(routes=["/", "/about"]))
    monkeypatch.setattr("deepagents.create_deep_agent", lambda **_kwargs: fake_graph)

    routes = await audit_crawler.discover_routes("http://127.0.0.1:4200")

    assert routes == ["/", "/about"]


async def test_discover_routes_returns_empty_list_when_no_structured_response(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_graph = _FakeDiscoveryGraph(None)
    monkeypatch.setattr("deepagents.create_deep_agent", lambda **_kwargs: fake_graph)

    routes = await audit_crawler.discover_routes("http://127.0.0.1:4200")

    assert routes == []


async def test_discover_routes_never_raises_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(**_kwargs: object) -> None:
        msg = "boom"
        raise RuntimeError(msg)

    monkeypatch.setattr("deepagents.create_deep_agent", _raise)

    routes = await audit_crawler.discover_routes("http://127.0.0.1:4200")

    assert routes == []


async def test_discover_and_audit_uses_discovered_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_discover_routes(base_url: str, *, model: str = "") -> list[str]:  # noqa: ARG001
        return ["/", "/about"]

    monkeypatch.setattr(audit_crawler, "discover_routes", _fake_discover_routes)
    runner = MagicMock(host="127.0.0.1", port=4200)
    runner.audit_pages.return_value = {"ok": True}

    result = await audit_crawler.discover_and_audit(runner)

    runner.start_server.assert_called_once()
    runner.audit_pages.assert_called_once_with(pages=("/", "/about"))
    runner.stop_server.assert_called_once()
    assert result == {"ok": True}


async def test_discover_and_audit_falls_back_to_fallback_pages_when_discovery_finds_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`discover_and_audit()` is the only audit path now - it runs for every
    repo, including the bundled Hallucinate.io fixture - so its own-route
    discovery-failure fallback must be app-agnostic, not a Hallucinate.io-
    specific route list. The fallback is `FALLBACK_PAGES = ("/",)`.
    """
    async def _fake_discover_routes(base_url: str, *, model: str = "") -> list[str]:  # noqa: ARG001
        return []

    monkeypatch.setattr(audit_crawler, "discover_routes", _fake_discover_routes)
    runner = MagicMock(host="127.0.0.1", port=4200)
    runner.audit_pages.return_value = {"ok": True}

    await audit_crawler.discover_and_audit(runner)

    runner.audit_pages.assert_called_once_with(pages=audit_crawler.FALLBACK_PAGES)
    assert audit_crawler.FALLBACK_PAGES == ("/",)


async def test_discover_and_audit_stops_server_even_if_audit_pages_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_discover_routes(base_url: str, *, model: str = "") -> list[str]:  # noqa: ARG001
        return ["/"]

    monkeypatch.setattr(audit_crawler, "discover_routes", _fake_discover_routes)
    runner = MagicMock(host="127.0.0.1", port=4200)
    runner.audit_pages.side_effect = RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await audit_crawler.discover_and_audit(runner)

    runner.stop_server.assert_called_once()


async def test_codebase_compiler_spec_permission_ordering() -> None:
    spec = await codebase_compiler.build("ollama:llama3.1")
    assert spec["name"] == "codebase_compiler"

    permissions = spec["permissions"]
    modes = [p.mode for p in permissions]
    # allow rules for read/write must precede the catch-all write deny,
    # since FilesystemPermission checking is first-match-wins.
    assert modes[-1] == "deny"
    assert modes[:-1] == ["allow", "allow"]

    write_allow = next(p for p in permissions if p.mode == "allow" and "write" in p.operations)
    virtual_fixture = config.to_virtual_path(config.fixture_path())
    assert all(path.startswith(virtual_fixture) for path in write_allow.paths)
    assert any(path.endswith("*.component.html") for path in write_allow.paths)
    assert any(path.endswith("src/index.html") for path in write_allow.paths)

    deny_rule = permissions[-1]
    assert deny_rule.paths == ["/**"]
    assert deny_rule.operations == ["write"]
