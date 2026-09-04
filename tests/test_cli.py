from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from langgraph.types import Command

from a11y_fixer import cli, config
from a11y_fixer.adapters.pr import delivery as pr_delivery
from a11y_fixer.adapters.pr.delivery import DryRunResult
from a11y_fixer.adapters.pr.github_pr_manager import PRCloseResult, PRMergeResult
from a11y_fixer.agents import audit_crawler
from a11y_fixer.deep_agent import ViolationResponse


@pytest.fixture(autouse=True)
def _no_real_dependency_install(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_apply_repo_override()` now also runs `ensure_dependencies_installed()`
    on `config.fixture_path()` when no `--repo` is given (closing the gap
    where only a `--repo`-resolved path got dependency-install coverage).
    Most tests here call `run`/`audit` (or `_apply_repo_override` directly)
    without pointing `A11Y_FIXTURE_PATH` at an isolated `tmp_path`, so
    without this, that call would resolve to the real, bundled
    Hallucinate.io checkout and touch its actual `node_modules` state (or
    worse, shell out to a real `npm install`) as an unrelated side effect
    of unit tests. Tests that specifically want the real behavior undo this
    with their own `monkeypatch.setattr(cli, "ensure_dependencies_installed", ...)`.
    """
    monkeypatch.setattr(cli, "ensure_dependencies_installed", lambda path: None)  # noqa: ARG005


def test_build_parser_audit_defaults() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["audit", "--repo", "/tmp/x"])
    assert args.command == "audit"
    assert args.repo == "/tmp/x"
    assert args.url is None


def test_build_parser_audit_rejects_conflicting_target_flags() -> None:
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["audit", "--repo", "/tmp/x", "--url", "https://example.com"])


def test_build_parser_audit_requires_a_target() -> None:
    """No implicit default target anymore - `audit` with neither `--repo`
    nor `--url` must fail fast at the argparse level, not silently fall
    back to the bundled Hallucinate.io fixture.
    """
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["audit"])


def test_build_parser_run_accepts_url_flag_alongside_repo() -> None:
    """`--url` is a real `run` flag now (audit a live site, still fix/PR
    against `--repo`) - argparse itself has no reason to reject it; the
    "requires --repo" rule is enforced at runtime in `_acmd_run`, not here.
    """
    parser = cli.build_parser()
    args = parser.parse_args(
        ["run", "--repo", "https://github.com/ACME/their-app.git", "--url", "https://example.com"]
    )
    assert args.url == "https://example.com"
    assert args.repo == "https://github.com/ACME/their-app.git"


def test_build_parser_run_live_flags() -> None:
    parser = cli.build_parser()
    assert parser.parse_args(["run", "--live"]).live is True
    assert parser.parse_args(["run", "--no-live"]).live is False
    assert parser.parse_args(["run"]).live is None


def test_build_parser_run_rejects_conflicting_live_flags() -> None:
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "--live", "--no-live"])


def test_build_parser_review_list_flag() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["review", "--list"])
    assert args.command == "review"
    assert args.list is True
    assert args.item is None


def test_build_parser_review_rejects_conflicting_decision_flags() -> None:
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["review", "1-x.json", "--approve", "--reject"])


def _patch_review_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "hitl_queue_dir", lambda: tmp_path / "hitl_queue")
    monkeypatch.setattr(config, "wiki_dir", lambda: tmp_path / "wiki")
    monkeypatch.setattr(config, "agent_root", lambda: tmp_path)


def test_cmd_review_list_empty_queue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    _patch_review_config(monkeypatch, tmp_path)
    parser = cli.build_parser()
    args = parser.parse_args(["review", "--list"])

    exit_code = cli._cmd_review(args)  # noqa: SLF001

    assert exit_code == 0
    assert "empty" in capsys.readouterr().out


def test_cmd_review_missing_item_and_decision_prints_usage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_review_config(monkeypatch, tmp_path)
    parser = cli.build_parser()
    args = parser.parse_args(["review"])

    assert cli._cmd_review(args) == 1  # noqa: SLF001


def test_cmd_review_item_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_review_config(monkeypatch, tmp_path)
    parser = cli.build_parser()
    args = parser.parse_args(["review", "missing.json", "--approve"])

    assert cli._cmd_review(args) == 1  # noqa: SLF001


def test_cmd_review_approve_real_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    _patch_review_config(monkeypatch, tmp_path)
    queue_dir = tmp_path / "hitl_queue"
    queue_dir.mkdir(parents=True)
    (queue_dir / "1-image-alt.json").write_text(
        json.dumps(
            {
                "violation": {"rule": "image-alt", "url": "/about", "selector": "img", "html": "<img>"},
                "response": {"rationale": "descriptive alt text"},
                "changes": [{"path": "about.component.html", "old_content": "<img>\n", "new_content": '<img alt="logo">\n'}],
            }
        ),
        encoding="utf-8",
    )
    parser = cli.build_parser()
    args = parser.parse_args(["review", "1-image-alt.json", "--approve", "--reviewer", "bob"])

    exit_code = cli._cmd_review(args)  # noqa: SLF001

    assert exit_code == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["decision"] == "approve"
    assert printed["delivered"] is True


def test_cmd_audit_writes_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`audit` now always resolves `--repo` and goes through
    `audit_crawler.discover_and_audit()` - there is no more implicit
    default-fixture path that calls `AxeAuditRunner.run()` directly, so
    this (and the discovery-path behavior itself) is exercised together
    here rather than in a separate, now-redundant test.
    """
    fake_report = {"total_violation_instances": 3, "pages": [{"url": "/", "violation_rules": ["html-has-lang"]}]}

    async def _fake_discover_and_audit(runner: object) -> dict:  # noqa: ARG001
        return fake_report

    monkeypatch.setattr(audit_crawler, "discover_and_audit", _fake_discover_and_audit)
    # `--repo` makes `_apply_repo_override` set `A11Y_FIXTURE_PATH` via a
    # direct `os.environ[...] = ...` mutation, not through `monkeypatch` -
    # priming it here (before that happens) is what makes monkeypatch's
    # teardown actually restore it afterwards, instead of leaking a deleted
    # tmp_path into every later test that calls `config.fixture_path()`.
    monkeypatch.setenv("A11Y_FIXTURE_PATH", "")

    # Mock config.agent_root to return tmp_path, so output goes there
    monkeypatch.setattr(config, "agent_root", lambda: tmp_path)
    expected_output = tmp_path / "evaluation" / "results" / "audit.json"

    parser = cli.build_parser()
    args = parser.parse_args(["audit", "--repo", str(tmp_path)])

    exit_code = cli._cmd_audit(args)  # noqa: SLF001

    assert exit_code == 0
    assert json.loads(expected_output.read_text(encoding="utf-8")) == fake_report


def test_cmd_audit_requires_a_target(capsys: pytest.CaptureFixture) -> None:
    """Defense in depth alongside the argparse-level `required=True` group:
    `_cmd_audit` itself also refuses to run with neither `--repo` nor
    `--url` set, in case it's ever called directly with a hand-built
    `Namespace` that bypassed argument parsing.
    """
    parser = cli.build_parser()
    args = parser.parse_args(["audit", "--repo", "/tmp/x"])
    args.repo = None  # simulate a caller bypassing argparse's own validation

    exit_code = cli._cmd_audit(args)  # noqa: SLF001

    assert exit_code == 2
    assert "one of --repo or --url is required" in capsys.readouterr().out


async def test_acmd_run_uses_crawler_discovery_when_repo_given(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # See test_cmd_audit_writes_report's comment: prime A11Y_FIXTURE_PATH
    # with monkeypatch before `_apply_repo_override` mutates it directly,
    # so it's restored afterwards instead of leaking into later tests.
    monkeypatch.setenv("A11Y_FIXTURE_PATH", "")

    async def _fake_discover_and_audit(runner: object) -> dict:  # noqa: ARG001
        return {"raw_reports": []}

    monkeypatch.setattr(audit_crawler, "discover_and_audit", _fake_discover_and_audit)

    parser = cli.build_parser()
    args = parser.parse_args(["run", "--repo", str(tmp_path)])

    exit_code = await cli._acmd_run(args)  # noqa: SLF001

    assert exit_code == 0


async def test_acmd_run_requires_an_explicit_target(capsys: pytest.CaptureFixture) -> None:
    """No implicit default target anymore - `run` needs an explicit
    `--repo`, `--url`, or `--audit`; with none given it must fail fast
    with a clear message instead of silently falling back to the bundled
    Hallucinate.io fixture. Unlike `audit`, this isn't enforced at the
    argparse level (`--audit` isn't part of a mutually-exclusive group
    with `--repo`/`--url`), so it's checked here in `_acmd_run` itself.
    """
    parser = cli.build_parser()
    args = parser.parse_args(["run"])

    exit_code = await cli._acmd_run(args)  # noqa: SLF001

    assert exit_code == 2
    assert "one of --repo, --url, or --audit is required" in capsys.readouterr().out


async def test_acmd_run_with_url_but_no_repo_rejects_with_clear_message(
    capsys: pytest.CaptureFixture,
) -> None:
    """--url alone gives the pipeline nothing to write fixes into and no repo
    to open PRs against - this must fail fast with a clear message rather
    than silently falling back to fixing/PR-ing the bundled Hallucinate.io
    fixture while auditing someone else's live site.
    """
    parser = cli.build_parser()
    args = parser.parse_args(["run", "--url", "https://acme.example.com"])

    exit_code = await cli._acmd_run(args)  # noqa: SLF001

    assert exit_code == 2
    assert "--url requires --repo" in capsys.readouterr().out


async def test_acmd_run_with_url_and_repo_audits_the_live_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--url + --repo: audit the live site directly, but still resolve
    --repo (so fixes are written to it and PRs target its derived
    GITHUB_REPO) rather than falling back to the bundled fixture.
    """
    cloned = tmp_path / "their-app"
    cloned.mkdir()
    monkeypatch.setattr(
        cli,
        "resolve_repo_source",
        lambda repo_arg, cache_dir: cloned,  # noqa: ARG005
    )
    monkeypatch.setenv("GITHUB_REPO", "")
    monkeypatch.setenv("A11Y_FIXTURE_PATH", "")

    captured_urls: list[str] = []

    async def _fake_audit_live_url(url: str) -> dict:
        captured_urls.append(url)
        return {"raw_reports": []}

    monkeypatch.setattr(cli, "_audit_live_url", _fake_audit_live_url)

    parser = cli.build_parser()
    args = parser.parse_args(
        ["run", "--repo", "https://github.com/ACME/their-app.git", "--url", "https://acme.example.com"]
    )

    exit_code = await cli._acmd_run(args)  # noqa: SLF001

    assert exit_code == 0
    assert captured_urls == ["https://acme.example.com"]
    assert os.environ["A11Y_FIXTURE_PATH"] == str(cloned)
    assert os.environ["GITHUB_REPO"] == "ACME/their-app"


def test_cmd_audit_url_mode_joins_discovered_routes_with_the_base_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_report = {"total_violation_instances": 0, "pages": []}
    captured_urls: list[str] = []

    async def _fake_discover_routes(base_url: str, *, model: str = "") -> list[str]:  # noqa: ARG001
        return ["/", "/about"]

    def _fake_audit_urls(self: object, urls: list[str]) -> dict:  # noqa: ARG001
        captured_urls.extend(urls)
        return fake_report

    monkeypatch.setattr(audit_crawler, "discover_routes", _fake_discover_routes)
    monkeypatch.setattr(cli.AxeAuditRunner, "audit_urls", _fake_audit_urls)

    # Mock config.agent_root to return tmp_path, so output goes there
    monkeypatch.setattr(config, "agent_root", lambda: tmp_path)
    expected_output = tmp_path / "evaluation" / "results" / "audit.json"

    parser = cli.build_parser()
    args = parser.parse_args(["audit", "--url", "https://example.com"])

    exit_code = cli._cmd_audit(args)  # noqa: SLF001

    assert exit_code == 0
    assert captured_urls == ["https://example.com/", "https://example.com/about"]
    assert json.loads(expected_output.read_text(encoding="utf-8")) == fake_report


def test_cmd_audit_url_mode_falls_back_to_the_given_url_when_discovery_finds_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured_urls: list[str] = []

    async def _fake_discover_routes(base_url: str, *, model: str = "") -> list[str]:  # noqa: ARG001
        return []

    def _fake_audit_urls(self: object, urls: list[str]) -> dict:  # noqa: ARG001
        captured_urls.extend(urls)
        return {"total_violation_instances": 0, "pages": []}

    monkeypatch.setattr(audit_crawler, "discover_routes", _fake_discover_routes)
    monkeypatch.setattr(cli.AxeAuditRunner, "audit_urls", _fake_audit_urls)

    # Mock config.agent_root to return tmp_path, so output goes there
    monkeypatch.setattr(config, "agent_root", lambda: tmp_path)

    parser = cli.build_parser()
    args = parser.parse_args(["audit", "--url", "https://example.com/"])

    exit_code = cli._cmd_audit(args)  # noqa: SLF001

    assert exit_code == 0
    assert captured_urls == ["https://example.com/"]


class _FakeInterrupt:
    def __init__(self, value: dict) -> None:
        self.value = value


async def test_resolve_interrupts_auto_approve_resumes_until_clean() -> None:
    interrupted_result = {"__interrupt__": [_FakeInterrupt({"action_requests": [{"description": "write fix"}]})]}
    final_result = {"messages": [], "structured_response": None}
    graph = MagicMock()
    graph.ainvoke = AsyncMock(return_value=final_result)

    result = await cli.resolve_interrupts(graph, {"configurable": {"thread_id": "t1"}}, interrupted_result, auto_approve=True)

    assert result is final_result
    graph.ainvoke.assert_called_once()
    (call_args, call_kwargs) = graph.ainvoke.call_args
    resumed_command = call_args[0]
    assert isinstance(resumed_command, Command)
    assert resumed_command.resume == {"decisions": [{"type": "approve"}]}


async def test_resolve_interrupts_manual_reject(monkeypatch: pytest.MonkeyPatch) -> None:
    interrupted_result = {"__interrupt__": [_FakeInterrupt({"action_requests": [{"description": "write fix"}]})]}
    final_result = {"messages": [], "structured_response": None}
    graph = MagicMock()
    graph.ainvoke = AsyncMock(return_value=final_result)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "n")

    await cli.resolve_interrupts(graph, {"configurable": {"thread_id": "t1"}}, interrupted_result, auto_approve=False)

    resumed_command = graph.ainvoke.call_args[0][0]
    assert resumed_command.resume["decisions"][0]["type"] == "reject"


async def test_resolve_interrupts_passthrough_when_not_interrupted() -> None:
    final_result = {"messages": [], "structured_response": None}
    graph = MagicMock()
    graph.ainvoke = AsyncMock()

    result = await cli.resolve_interrupts(graph, {}, final_result, auto_approve=True)

    assert result is final_result
    graph.ainvoke.assert_not_called()


def _violation() -> dict:
    return {"rule": "color-contrast", "url": "/product", "selector": ".cta", "html": "<button>Buy</button>"}


def test_deliver_violation_routes_human_to_review_queue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "hitl_queue_dir", lambda: tmp_path / "hitl_queue")
    monkeypatch.setattr("a11y_fixer.config.agent_root", lambda: tmp_path / "agent")
    (tmp_path / "agent").mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)  # noqa: S603, S607
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)  # noqa: S603, S607
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)  # noqa: S603, S607
    (repo / "blog.component.html").write_text("<button>Buy</button>\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)  # noqa: S603, S607
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)  # noqa: S603, S607
    (repo / "blog.component.html").write_text('<button aria-label="Buy">Buy</button>\n', encoding="utf-8")

    response = ViolationResponse(
        rule="color-contrast", wcag="1.4.3", selector=".cta", technique_id="G18", technique_type="sufficient",
        code="", rationale="ambiguous background", score=10.0, route="human",
    )

    outcome = cli.deliver_violation(
        _violation(), response, fixture=repo, pr_config=config.PRDeliveryConfig(live=False, github_token=None, github_repo=None),
        output_dir=tmp_path / "prs",
    )

    assert outcome["delivered"] is False
    assert Path(outcome["queue_path"]).exists()
    # working tree reset even on the human path (Phase 2 fix)
    assert (repo / "blog.component.html").read_text(encoding="utf-8") == "<button>Buy</button>\n"


def test_capture_and_reset_git_changes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)  # noqa: S603, S607
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)  # noqa: S603, S607
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)  # noqa: S603, S607
    (repo / "index.html").write_text("<html>\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)  # noqa: S603, S607
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)  # noqa: S603, S607

    (repo / "index.html").write_text('<html lang="en">\n', encoding="utf-8")

    changes = cli._capture_and_reset_git_changes(repo)  # noqa: SLF001

    assert len(changes) == 1
    assert changes[0].path == "index.html"
    assert changes[0].old_content == "<html>\n"
    assert changes[0].new_content == '<html lang="en">\n'
    # working tree reset back to HEAD
    assert (repo / "index.html").read_text(encoding="utf-8") == "<html>\n"


def test_deliver_violation_routes_auto_to_dry_run_pr(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)  # noqa: S603, S607
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)  # noqa: S603, S607
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)  # noqa: S603, S607
    (repo / "about.component.html").write_text("<img>\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)  # noqa: S603, S607
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)  # noqa: S603, S607
    (repo / "about.component.html").write_text('<img alt="logo">\n', encoding="utf-8")

    response = ViolationResponse(
        rule="image-alt", wcag="1.1.1", selector="img", technique_id="H37", technique_type="sufficient",
        code='<img alt="logo">', rationale="descriptive alt text", score=20.0, route="auto",
    )
    outcome = cli.deliver_violation(
        {"rule": "image-alt", "url": "/about", "selector": "img", "html": "<img>"},
        response,
        fixture=repo,
        pr_config=config.PRDeliveryConfig(live=False, github_token=None, github_repo=None),
        output_dir=tmp_path / "prs",
    )

    assert outcome["delivered"] is True
    assert isinstance(outcome["result"], DryRunResult)
    assert outcome["result"].diff_path.exists()
    assert outcome["route"] == "auto"


def test_deliver_violation_assess_risk_overrides_auto_to_human_for_high_risk_rule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """assess_risk may escalate a model's "auto" self-report - html-has-lang is
    on HIGH_RISK_RULES (site-wide blast radius) regardless of rubric score.
    """
    monkeypatch.setattr("a11y_fixer.config.agent_root", lambda: tmp_path / "agent")
    (tmp_path / "agent").mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)  # noqa: S603, S607
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)  # noqa: S603, S607
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)  # noqa: S603, S607
    (repo / "index.html").write_text("<html>\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)  # noqa: S603, S607
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)  # noqa: S603, S607
    (repo / "index.html").write_text('<html lang="en">\n', encoding="utf-8")

    response = ViolationResponse(
        rule="html-has-lang", wcag="3.1.1", selector="html", technique_id="H57", technique_type="sufficient",
        code='<html lang="en">', rationale="site-wide language fix", score=20.0, route="auto",
    )
    outcome = cli.deliver_violation(
        {"rule": "html-has-lang", "url": "/", "selector": "html", "html": "<html>"},
        response,
        fixture=repo,
        pr_config=config.PRDeliveryConfig(live=False, github_token=None, github_repo=None),
        output_dir=tmp_path / "prs",
    )

    assert outcome["delivered"] is False
    assert outcome["route"] == "human"
    queued = json.loads(Path(outcome["queue_path"]).read_text(encoding="utf-8"))
    assert queued["risk_assessments"][0]["high_blast_radius"] is True


def test_deliver_violation_verified_deterministic_waives_blast_radius(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The html-lang fast-track's carve-out (2026-09): verified_deterministic=True
    lets a build-verified html-has-lang fix auto-deliver despite touching the
    high-blast-radius src/index.html - unlike the plain LLM-driven path covered
    by test_deliver_violation_assess_risk_overrides_auto_to_human_for_high_risk_rule
    above, which doesn't pass the flag and still (correctly) routes human.
    """
    monkeypatch.setattr("a11y_fixer.config.agent_root", lambda: tmp_path / "agent")
    (tmp_path / "agent").mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)  # noqa: S603, S607
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)  # noqa: S603, S607
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)  # noqa: S603, S607
    (repo / "index.html").write_text("<html>\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)  # noqa: S603, S607
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)  # noqa: S603, S607
    (repo / "index.html").write_text('<html lang="en">\n', encoding="utf-8")

    response = ViolationResponse(
        rule="html-has-lang", wcag="3.1.1", selector="html", technique_id="H57", technique_type="sufficient",
        code='<html lang="en">', rationale="site-wide language fix", score=20.0, route="auto",
    )
    outcome = cli.deliver_violation(
        {"rule": "html-has-lang", "url": "/", "selector": "html", "html": "<html>"},
        response,
        fixture=repo,
        pr_config=config.PRDeliveryConfig(live=False, github_token=None, github_repo=None),
        output_dir=tmp_path / "prs",
        verified_deterministic=True,
    )

    assert outcome["delivered"] is True
    assert outcome["route"] == "auto"
    assert isinstance(outcome["result"], DryRunResult)


def test_deliver_violation_verified_deterministic_still_escalates_low_confidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The carve-out must not become a blanket bypass: a low-score fix on the
    same high-blast-radius file still escalates to human even with
    verified_deterministic=True - the flag only waives blast radius, never
    low_confidence (mirrors test_verified_deterministic_does_not_waive_low_confidence
    in tests/domain/test_hitl_policy.py, but through the real deliver_violation()
    call site instead of assess_risk() directly).
    """
    monkeypatch.setattr("a11y_fixer.config.agent_root", lambda: tmp_path / "agent")
    (tmp_path / "agent").mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)  # noqa: S603, S607
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)  # noqa: S603, S607
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)  # noqa: S603, S607
    (repo / "index.html").write_text("<html>\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)  # noqa: S603, S607
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)  # noqa: S603, S607
    (repo / "index.html").write_text('<html lang="en">\n', encoding="utf-8")

    response = ViolationResponse(
        rule="html-has-lang", wcag="3.1.1", selector="html", technique_id="H57", technique_type="sufficient",
        code='<html lang="en">', rationale="uncertain", score=8.0, route="auto",
    )
    outcome = cli.deliver_violation(
        {"rule": "html-has-lang", "url": "/", "selector": "html", "html": "<html>"},
        response,
        fixture=repo,
        pr_config=config.PRDeliveryConfig(live=False, github_token=None, github_repo=None),
        output_dir=tmp_path / "prs",
        verified_deterministic=True,
    )

    assert outcome["delivered"] is False
    assert outcome["route"] == "human"


def test_deliver_violation_path_guardrail_escalates_disallowed_extension(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """validate_write_path (2b) escalates on its own, independent of assess_risk."""
    monkeypatch.setattr("a11y_fixer.config.agent_root", lambda: tmp_path / "agent")
    (tmp_path / "agent").mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)  # noqa: S603, S607
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)  # noqa: S603, S607
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)  # noqa: S603, S607
    (repo / "about.component.json").write_text("{}\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)  # noqa: S603, S607
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)  # noqa: S603, S607
    (repo / "about.component.json").write_text('{"alt": "logo"}\n', encoding="utf-8")

    response = ViolationResponse(
        rule="image-alt", wcag="1.1.1", selector="img", technique_id="H37", technique_type="sufficient",
        code='{"alt": "logo"}', rationale="descriptive alt text", score=20.0, route="auto",
    )
    outcome = cli.deliver_violation(
        {"rule": "image-alt", "url": "/about", "selector": "img", "html": "<img>"},
        response,
        fixture=repo,
        pr_config=config.PRDeliveryConfig(live=False, github_token=None, github_repo=None),
        output_dir=tmp_path / "prs",
    )

    assert outcome["delivered"] is False
    assert outcome["route"] == "human"
    queued = json.loads(Path(outcome["queue_path"]).read_text(encoding="utf-8"))
    assert queued["path_violations"]  # .json isn't in ALLOWED_WRITE_EXTENSIONS
    # isolated from assess_risk: this rule/file/score would have been "auto" on its own
    assert queued["risk_assessments"][0]["route"] == "auto"


def test_deliver_violation_epistemic_gate_recorded_on_low_confidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """epistemic_gate (2c) is genuinely called and its verdict recorded - even though it
    always agrees with assess_risk's own low_confidence check at this call site today
    (both derive from the same p_ik = score / 20, and 15/20 == 0.75 exactly).
    """
    monkeypatch.setattr("a11y_fixer.config.agent_root", lambda: tmp_path / "agent")
    (tmp_path / "agent").mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)  # noqa: S603, S607
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)  # noqa: S603, S607
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)  # noqa: S603, S607
    (repo / "about.component.html").write_text("<img>\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)  # noqa: S603, S607
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)  # noqa: S603, S607
    (repo / "about.component.html").write_text('<img alt="logo">\n', encoding="utf-8")

    response = ViolationResponse(
        rule="image-alt", wcag="1.1.1", selector="img", technique_id="H37", technique_type="sufficient",
        code='<img alt="logo">', rationale="uncertain alt text", score=8.0, route="auto",
    )
    outcome = cli.deliver_violation(
        {"rule": "image-alt", "url": "/about", "selector": "img", "html": "<img>"},
        response,
        fixture=repo,
        pr_config=config.PRDeliveryConfig(live=False, github_token=None, github_repo=None),
        output_dir=tmp_path / "prs",
    )

    assert outcome["route"] == "human"
    queued = json.loads(Path(outcome["queue_path"]).read_text(encoding="utf-8"))
    assert queued["epistemic_gate"]["verdict"] == "BLOCK"
    assert queued["epistemic_gate"]["p_ik"] == 0.4


def test_deliver_violation_reports_no_changes(tmp_path: Path) -> None:
    # rule must be OFF the HIGH_RISK_RULES / HIGH_BLAST_RADIUS lists so this
    # stays a genuine "auto route, nothing captured, nothing to do" case -
    # see test_deliver_violation_escalates_to_queue_with_no_file_changes
    # below for the "no changes but still must escalate" case (case-10).
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)  # noqa: S603, S607
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)  # noqa: S603, S607
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)  # noqa: S603, S607
    (repo / "features.component.html").write_text("<a></a>\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)  # noqa: S603, S607
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)  # noqa: S603, S607

    response = ViolationResponse(
        rule="link-name", wcag="1.1.1", selector=".element-5333", technique_id="H30", technique_type="sufficient",
        code="", rationale="no-op", score=20.0, route="auto",
    )
    outcome = cli.deliver_violation(
        {"rule": "link-name", "url": "/features", "selector": ".element-5333", "html": "<a></a>"},
        response,
        fixture=repo,
        pr_config=config.PRDeliveryConfig(live=False, github_token=None, github_repo=None),
        output_dir=tmp_path / "prs",
    )

    assert outcome["delivered"] is False
    assert outcome["route"] == "auto"
    assert "reason" in outcome


def test_deliver_violation_escalates_to_queue_with_no_file_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression test for case-10 (2026-09-03): the agent's response called
    # for human review, but codebase_compiler happened to leave no captured
    # git diff (empty response, or a race with a prior reset). That must
    # still land a HITL queue entry, not be silently dropped.
    monkeypatch.setattr(config, "hitl_queue_dir", lambda: tmp_path / "hitl_queue")
    monkeypatch.setattr("a11y_fixer.config.agent_root", lambda: tmp_path / "agent")
    (tmp_path / "agent").mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)  # noqa: S603, S607
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)  # noqa: S603, S607
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)  # noqa: S603, S607
    (repo / "features.component.html").write_text("<a></a>\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)  # noqa: S603, S607
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)  # noqa: S603, S607
    # no post-commit edit: fixture has zero uncommitted diff at delivery time

    response = ViolationResponse(
        rule="link-name", wcag="1.1.1", selector=".element-5333", technique_id="H30", technique_type="sufficient",
        code="", rationale="agent reported the fix but produced no diff", score=15.0, route="human",
    )
    outcome = cli.deliver_violation(
        {"rule": "link-name", "url": "/features", "selector": ".element-5333", "html": "<a></a>"},
        response,
        fixture=repo,
        pr_config=config.PRDeliveryConfig(live=False, github_token=None, github_repo=None),
        output_dir=tmp_path / "prs",
    )

    assert outcome["delivered"] is False
    assert outcome["route"] == "human"
    assert "queue_path" in outcome
    queue_path = Path(outcome["queue_path"])
    assert queue_path.exists()

    queued = json.loads(queue_path.read_text(encoding="utf-8"))
    assert queued["no_file_changes"] is True
    assert queued["changes"] == []


def _audit_report(cases: list[tuple[str, str]]) -> dict:
    """Build a minimal normalized-report payload `flatten_violation_instances` accepts."""
    return {
        "raw_reports": [
            {
                "url": url,
                "violations": [{"id": rule, "tags": [], "nodes": [{"html": f"<{rule}>", "target": [rule], "failureSummary": None}]}],
            }
            for url, rule in cases
        ]
    }


def test_cmd_run_continues_after_one_violation_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("a11y_fixer.config.agent_root", lambda: tmp_path / "agent")
    (tmp_path / "agent").mkdir()
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(json.dumps(_audit_report([("/a", "rule-a"), ("/b", "rule-b")])), encoding="utf-8")

    invoked: list[str] = []

    class FakeGraph:
        async def ainvoke(self, messages: dict, config: dict) -> dict:  # noqa: ARG002
            invoked.append(config["configurable"]["thread_id"])
            # Only violation-0-attempt1 raises exception; everything else returns None
            if config["configurable"]["thread_id"] == "violation-0-attempt1":
                msg = "boom"
                raise RuntimeError(msg)
            # All other calls return None (structured response)
            return {"structured_response": None}

    async def fake_abuild_agent(**_kwargs: object) -> FakeGraph:
        return FakeGraph()

    monkeypatch.setattr("a11y_fixer.deep_agent.abuild_agent", fake_abuild_agent)

    parser = cli.build_parser()
    args = parser.parse_args(["run", "--audit", str(audit_path), "--no-live", "--yes"])

    exit_code = cli._cmd_run(args)  # noqa: SLF001

    # First violation (rule-a) has 1 exception on attempt1, continues to violation-1
    # Second violation (rule-b) has 3 None retries (no structured response after retries)
    # Total: 4 calls (1 for first, 3 for second)
    assert len(invoked) == 4
    assert invoked[0] == "violation-0-attempt1"
    assert invoked[1] == "violation-1-attempt1"
    assert invoked[2] == "violation-1-attempt2"
    assert invoked[3] == "violation-1-attempt3"
    assert exit_code == 1


def test_cmd_run_rejects_malformed_audit_report_before_building_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(json.dumps({"raw_reports": [{"violations": []}]}), encoding="utf-8")  # missing "url"

    async def fake_abuild_agent(**_kwargs: object) -> None:
        raise AssertionError("abuild_agent should not be called for a malformed audit report")

    monkeypatch.setattr("a11y_fixer.deep_agent.abuild_agent", fake_abuild_agent)

    parser = cli.build_parser()
    args = parser.parse_args(["run", "--audit", str(audit_path), "--no-live", "--yes"])

    exit_code = cli._cmd_run(args)  # noqa: SLF001

    assert exit_code == 2


def test_cmd_run_warns_on_overconfident_rationale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("a11y_fixer.config.agent_root", lambda: tmp_path / "agent")
    (tmp_path / "agent").mkdir()
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(json.dumps(_audit_report([("/a", "rule-a")])), encoding="utf-8")

    overconfident_response = ViolationResponse(
        rule="rule-a", wcag="1.1.1", selector="img", technique_id="H37", technique_type="sufficient",
        code='<img alt="x">', rationale="This fix is guaranteed to always pass with zero risk.",
        score=20.0, route="auto",
    )

    class FakeGraph:
        async def ainvoke(self, messages: dict, config: dict) -> dict:  # noqa: ARG002
            return {"structured_response": overconfident_response}

    async def fake_abuild_agent(**_kwargs: object) -> FakeGraph:
        return FakeGraph()

    monkeypatch.setattr("a11y_fixer.deep_agent.abuild_agent", fake_abuild_agent)
    monkeypatch.setattr(cli, "deliver_violation", lambda *_a, **_k: {"delivered": False, "reason": "test stub"})

    parser = cli.build_parser()
    args = parser.parse_args(["run", "--audit", str(audit_path), "--no-live", "--yes"])

    exit_code = cli._cmd_run(args)  # noqa: SLF001

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "confidence-calibration FAIL" in captured.out


def test_warn_on_overconfidence_is_silent_for_well_calibrated_text(capsys: pytest.CaptureFixture[str]) -> None:
    cli.warn_on_overconfidence("rule-a", "Evidence suggests this improves contrast for most users.")

    assert capsys.readouterr().out == ""



def _init_git_fixture(repo: Path, filename: str, before: str, after: str) -> None:
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)  # noqa: S603, S607
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)  # noqa: S603, S607
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)  # noqa: S603, S607
    (repo / filename).write_text(before, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)  # noqa: S603, S607
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)  # noqa: S603, S607
    (repo / filename).write_text(after, encoding="utf-8")


def test_deliver_violation_auto_merge_success_runs_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Regression test (2026-09-04): auto_merge_pr() returns a PRMergeResult
    dataclass, but deliver_violation() used to do `merge_result.get("merged")`
    - an AttributeError on every call, silently swallowed, that made a real
    merge success print as "Auto-merge/cleanup failed" and left
    cleanup_duplicate_prs() unreachable. Asserts cleanup actually runs on a
    real success and the success is reported without the old crash message.
    """
    repo = tmp_path / "repo"
    _init_git_fixture(repo, "about.component.html", "<img>\n", '<img alt="logo">\n')

    response = ViolationResponse(
        rule="image-alt", wcag="1.1.1", selector="img", technique_id="H37", technique_type="sufficient",
        code='<img alt="logo">', rationale="descriptive alt text", score=20.0, route="auto",
    )

    live_result = pr_delivery.LiveResult(
        pull_request_url="https://github.com/owner/repo/pull/7",
        pull_request_number=7,
        branch_name="a11y-fixer/abc123",
    )
    monkeypatch.setattr(cli.pr_delivery, "deliver", lambda plan, config, output_dir: live_result)

    fake_manager = MagicMock()
    fake_manager.auto_merge_pr.return_value = PRMergeResult(
        success=True, pr_number=7, reason="auto_merged_high_score (20.0 >= 18.0)"
    )
    fake_manager.cleanup_duplicate_prs.return_value = [
        PRCloseResult(success=True, pr_number=3, reason="closed_as_duplicate_of_pr_7"),
    ]
    monkeypatch.setattr(cli, "GitHubPRManager", lambda **kwargs: fake_manager)  # noqa: ARG005

    outcome = cli.deliver_violation(
        {"rule": "image-alt", "url": "/about", "selector": "img", "html": "<img>"},
        response,
        fixture=repo,
        pr_config=config.PRDeliveryConfig(live=True, github_token="fake-token", github_repo="owner/repo"),
        output_dir=tmp_path / "prs",
    )

    assert outcome["delivered"] is True
    assert outcome["result"] is live_result
    fake_manager.cleanup_duplicate_prs.assert_called_once()
    _, cleanup_kwargs = fake_manager.cleanup_duplicate_prs.call_args
    assert cleanup_kwargs["kept_pr_number"] == 7

    captured = capsys.readouterr()
    assert "Auto-merged PR 7" in captured.out
    assert "Auto-merge/cleanup failed" not in captured.out


def test_deliver_violation_auto_merge_failure_skips_cleanup_without_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A merge that legitimately doesn't happen (GitHub rejects it, branch
    protection, etc.) must be reported honestly and must not run duplicate
    cleanup - only a confirmed merge earns that - and must not crash.
    """
    repo = tmp_path / "repo"
    _init_git_fixture(repo, "about.component.html", "<img>\n", '<img alt="logo">\n')

    response = ViolationResponse(
        rule="image-alt", wcag="1.1.1", selector="img", technique_id="H37", technique_type="sufficient",
        code='<img alt="logo">', rationale="descriptive alt text", score=19.0, route="auto",
    )

    live_result = pr_delivery.LiveResult(
        pull_request_url="https://github.com/owner/repo/pull/8",
        pull_request_number=8,
        branch_name="a11y-fixer/def456",
    )
    monkeypatch.setattr(cli.pr_delivery, "deliver", lambda plan, config, output_dir: live_result)

    fake_manager = MagicMock()
    fake_manager.auto_merge_pr.return_value = PRMergeResult(
        success=False, pr_number=8, reason="merge_failed (409)"
    )
    monkeypatch.setattr(cli, "GitHubPRManager", lambda **kwargs: fake_manager)  # noqa: ARG005

    outcome = cli.deliver_violation(
        {"rule": "image-alt", "url": "/about", "selector": "img", "html": "<img>"},
        response,
        fixture=repo,
        pr_config=config.PRDeliveryConfig(live=True, github_token="fake-token", github_repo="owner/repo"),
        output_dir=tmp_path / "prs",
    )

    assert outcome["delivered"] is True
    fake_manager.cleanup_duplicate_prs.assert_not_called()

    captured = capsys.readouterr()
    assert "merge_failed" in captured.out
    assert "Auto-merge/cleanup failed" not in captured.out



def test_apply_repo_override_derives_github_repo_from_url(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`--repo` pointed at a GitHub URL should also target PR delivery there.

    Regression test for the GITHUB_REPO-not-derived-from---repo gap: before
    this fix, `_apply_repo_override()` only ever set `A11Y_FIXTURE_PATH`, so
    `config.resolve_pr_delivery()` kept reading whatever static `GITHUB_REPO`
    happened to be exported - unrelated to the repo actually being fixed.
    """
    monkeypatch.setenv("GITHUB_REPO", "mdrmtz/Hallucinate.io")
    monkeypatch.setenv("A11Y_FIXTURE_PATH", "")
    cloned = tmp_path / "cache" / "their-app"
    cloned.mkdir(parents=True)
    monkeypatch.setattr(
        cli,
        "resolve_repo_source",
        lambda repo_arg, cache_dir: cloned,  # noqa: ARG005
    )

    cli._apply_repo_override("https://github.com/ACME/their-app.git")

    assert os.environ["GITHUB_REPO"] == "ACME/their-app"
    assert os.environ["A11Y_FIXTURE_PATH"] == str(cloned)


def test_apply_repo_override_derives_github_repo_from_local_checkout_remote(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    local_repo = tmp_path / "local-checkout"
    local_repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=local_repo, check=True)  # noqa: S603, S607
    subprocess.run(  # noqa: S603, S607
        ["git", "remote", "add", "origin", "https://github.com/ACME/their-app.git"],
        cwd=local_repo,
        check=True,
    )
    monkeypatch.setenv("GITHUB_REPO", "")
    monkeypatch.setenv("A11Y_FIXTURE_PATH", "")
    monkeypatch.setattr(
        cli,
        "resolve_repo_source",
        lambda repo_arg, cache_dir: local_repo,  # noqa: ARG005
    )

    cli._apply_repo_override(str(local_repo))

    assert os.environ["GITHUB_REPO"] == "ACME/their-app"


def test_apply_repo_override_leaves_github_repo_untouched_when_not_derivable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A --repo that isn't GitHub-shaped (and has no derivable origin) must not
    clobber a GITHUB_REPO value that may still be valid.
    """
    monkeypatch.setenv("GITHUB_REPO", "mdrmtz/Hallucinate.io")
    monkeypatch.setenv("A11Y_FIXTURE_PATH", "")
    local_repo = tmp_path / "no-remote-checkout"
    local_repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=local_repo, check=True)  # noqa: S603, S607
    monkeypatch.setattr(
        cli,
        "resolve_repo_source",
        lambda repo_arg, cache_dir: local_repo,  # noqa: ARG005
    )

    cli._apply_repo_override(str(local_repo))

    assert os.environ["GITHUB_REPO"] == "mdrmtz/Hallucinate.io"


def test_apply_repo_override_without_repo_arg_leaves_github_repo_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_REPO", "mdrmtz/Hallucinate.io")
    monkeypatch.setenv("A11Y_FIXTURE_PATH", "")

    cli._apply_repo_override(None)

    assert os.environ["GITHUB_REPO"] == "mdrmtz/Hallucinate.io"


def test_build_parser_fleet_requires_config() -> None:
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["fleet"])


def test_build_parser_fleet_defaults_to_live_dry_run_false() -> None:
    """`fleet` inverts `run`'s default: no `--dry-run` flag means live."""
    parser = cli.build_parser()
    args = parser.parse_args(["fleet", "--config", "sites.yaml"])
    assert args.dry_run is False
    assert args.config == "sites.yaml"


def test_build_parser_fleet_accepts_dry_run_flag() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["fleet", "--config", "sites.yaml", "--dry-run"])
    assert args.dry_run is True


def _write_manifest(tmp_path: Path, text: str) -> Path:
    manifest = tmp_path / "sites.yaml"
    manifest.write_text(text, encoding="utf-8")
    return manifest


def test_cmd_fleet_invalid_manifest_returns_2(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    parser = cli.build_parser()
    args = parser.parse_args(
        ["fleet", "--config", str(tmp_path / "does-not-exist.yaml")]
    )

    exit_code = cli._cmd_fleet(args)  # noqa: SLF001

    assert exit_code == 2
    assert "not found" in capsys.readouterr().out


def test_cmd_fleet_rejects_more_than_one_site(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    manifest = _write_manifest(
        tmp_path,
        "sites:\n"
        "  - repo: https://github.com/acme/one.git\n"
        "  - repo: https://github.com/acme/two.git\n",
    )
    parser = cli.build_parser()
    args = parser.parse_args(["fleet", "--config", str(manifest)])

    exit_code = cli._cmd_fleet(args)  # noqa: SLF001

    assert exit_code == 2
    assert "one site per invocation" in capsys.readouterr().out


def test_cmd_fleet_live_without_token_fails_fast_and_does_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setenv("ACME_GITHUB_TOKEN", "")
    fake_acmd_run = AsyncMock(return_value=0)
    monkeypatch.setattr(cli, "_acmd_run", fake_acmd_run)

    manifest = _write_manifest(
        tmp_path,
        "sites:\n"
        "  - repo: https://github.com/acme/one.git\n"
        "    github_token_env: ACME_GITHUB_TOKEN\n",
    )
    parser = cli.build_parser()
    args = parser.parse_args(["fleet", "--config", str(manifest)])

    exit_code = cli._cmd_fleet(args)  # noqa: SLF001

    assert exit_code == 2
    out = capsys.readouterr().out
    assert "ACME_GITHUB_TOKEN" in out
    assert "--dry-run" in out
    fake_acmd_run.assert_not_called()


def test_cmd_fleet_dry_run_does_not_require_a_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ACME_GITHUB_TOKEN", "")
    fake_acmd_run = AsyncMock(return_value=0)
    monkeypatch.setattr(cli, "_acmd_run", fake_acmd_run)

    manifest = _write_manifest(
        tmp_path,
        "sites:\n"
        "  - repo: https://github.com/acme/one.git\n"
        "    github_token_env: ACME_GITHUB_TOKEN\n",
    )
    parser = cli.build_parser()
    args = parser.parse_args(["fleet", "--config", str(manifest), "--dry-run"])

    exit_code = cli._cmd_fleet(args)  # noqa: SLF001

    assert exit_code == 0
    fake_acmd_run.assert_called_once()
    called_ns = fake_acmd_run.call_args[0][0]
    assert called_ns.live is False


def test_cmd_fleet_live_success_builds_expected_namespace_and_restores_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A live fleet run: prints the LIVE banner, temporarily exports the
    site's token as GITHUB_TOKEN for the duration of `_acmd_run` only, and
    calls `_acmd_run` with a Namespace equivalent to what `run --repo <repo>
    --url <url> --live --yes` would produce.
    """
    monkeypatch.setenv("ACME_GITHUB_TOKEN", "secret-token-value")
    monkeypatch.setenv("GITHUB_TOKEN", "previous-unrelated-token")

    observed_token_during_run: list[str | None] = []

    async def _fake_acmd_run(ns: object) -> int:
        observed_token_during_run.append(os.environ.get("GITHUB_TOKEN"))
        return 0

    monkeypatch.setattr(cli, "_acmd_run", _fake_acmd_run)

    manifest = _write_manifest(
        tmp_path,
        "sites:\n"
        "  - repo: https://github.com/acme/one.git\n"
        "    url: https://one.acme.com\n"
        "    site_id: acme-one\n"
        "    github_token_env: ACME_GITHUB_TOKEN\n",
    )
    parser = cli.build_parser()
    args = parser.parse_args(["fleet", "--config", str(manifest)])

    exit_code = cli._cmd_fleet(args)  # noqa: SLF001

    assert exit_code == 0
    assert "LIVE: acme-one -> https://github.com/acme/one.git" in capsys.readouterr().out
    assert observed_token_during_run == ["secret-token-value"]
    # The site's real token must never leak into the process env after the
    # run - GITHUB_TOKEN must be restored to whatever it was before.
    assert os.environ["GITHUB_TOKEN"] == "previous-unrelated-token"


def test_cmd_fleet_passes_repo_and_url_through_to_acmd_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    captured: list[object] = []

    async def _fake_acmd_run(ns: object) -> int:
        captured.append(ns)
        return 0

    monkeypatch.setattr(cli, "_acmd_run", _fake_acmd_run)

    manifest = _write_manifest(
        tmp_path,
        "sites:\n"
        "  - repo: https://github.com/acme/one.git\n"
        "    url: https://one.acme.com\n",
    )
    parser = cli.build_parser()
    args = parser.parse_args(["fleet", "--config", str(manifest)])

    exit_code = cli._cmd_fleet(args)  # noqa: SLF001

    assert exit_code == 0
    assert len(captured) == 1
    ns = captured[0]
    assert ns.repo == "https://github.com/acme/one.git"
    assert ns.url == "https://one.acme.com"
    assert ns.audit is None
    assert ns.case_ids is None
    assert ns.live is True
    assert ns.yes is True


def test_cmd_fleet_passes_audit_through_to_acmd_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """`audit:` in the manifest lets `fleet` replay a saved audit report
    (mirrors `run --audit`) instead of always crawling `repo`/`url` live -
    the fast-testing path for a known violation set.
    """
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    captured: list[object] = []

    async def _fake_acmd_run(ns: object) -> int:
        captured.append(ns)
        return 0

    monkeypatch.setattr(cli, "_acmd_run", _fake_acmd_run)

    audit_path = tmp_path / "audit.json"
    manifest = _write_manifest(
        tmp_path,
        "sites:\n"
        "  - repo: https://github.com/acme/one.git\n"
        f"    audit: {audit_path}\n",
    )
    parser = cli.build_parser()
    args = parser.parse_args(["fleet", "--config", str(manifest)])

    exit_code = cli._cmd_fleet(args)  # noqa: SLF001

    assert exit_code == 0
    assert len(captured) == 1
    ns = captured[0]
    assert ns.audit == str(audit_path)
    assert "replaying saved audit report" in capsys.readouterr().out
