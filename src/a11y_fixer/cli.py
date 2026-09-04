"""CLI entrypoint: the single composition boundary for running the agent.

`audit` runs a fresh axe-core report. `run` drives the deep agent over every
violation in a report (fresh or `--audit <path>`), resolving any HITL
interrupts (every `write_file`/`edit_file` pauses for approval - see
`deep_agent.abuild_agent`), then delivers `route: "auto"` results as a PR (or
dry-run diff, per `--live`/`--no-live`) and queues `route: "human"` results
for review.

Both subcommands require an explicit target - `--repo <path-or-url>` points
at any Angular repo to audit/fix (a local path is used as-is, a git URL is
shallow-cloned into `config.repo_cache_dir()`); `--url` audits a live site
directly; `run` also accepts `--audit <path>` alone to replay a saved
report. There is no implicit default target - the bundled Hallucinate.io
fixture must be pointed at explicitly like any other repo.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse

from langgraph.types import Command

from a11y_fixer import config
from a11y_fixer.adapters.audit_runner import AxeAuditRunner, flatten_violation_instances
from a11y_fixer.adapters.pr import delivery as pr_delivery
from a11y_fixer.adapters.pr.github_pr_manager import GitHubPRManager
from a11y_fixer.adapters.repo_source import (
    derive_github_repo,
    ensure_dependencies_installed,
    resolve_repo_source,
)
from a11y_fixer.fleet_config import FleetManifestError, load_manifest
from a11y_fixer.adapters.violation_store import (
    PrePipelineGate,
    HITLQueueGate,
    ViolationStore,
)
from a11y_fixer.adapters.html_lang_applier import apply_html_lang
from a11y_fixer.domain.guardrail_rules import (
    check_confidence_calibration,
    epistemic_gate,
    validate_raw_axe_reports,
    validate_write_path,
)
from a11y_fixer.domain.hitl_policy import assess_risk
from a11y_fixer.domain.html_lang_fix import (
    get_html_lang_fix,
    is_html_lang_violation,
)
from a11y_fixer.domain.violations import (
    compute_violation_id,
    ViolationStatus,
    ViolationState,
)
from a11y_fixer.hitl.review_queue import calibrate_from_results

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

    from a11y_fixer.deep_agent import ViolationResponse

DEFAULT_AUDIT_OUTPUT = "evaluation/results/audit.json"


def _apply_repo_override(repo_arg: str | None) -> None:
    """Resolve `--repo` (if given) and point every downstream module at it.

    Reuses the existing `A11Y_FIXTURE_PATH` override that `config.
    fixture_path()` already checks first - no other module needs to know
    about `--repo` at all. Prints the resolved path unconditionally so the
    target repo is always visible to whoever runs the command, not a hidden
    default.

    Also derives `GITHUB_REPO` from the same `--repo` value and exports it,
    so `config.resolve_pr_delivery()` targets PR delivery at the repo
    actually being fixed instead of whatever static `GITHUB_REPO` happens to
    be set in the environment - without this, a dynamically-targeted repo
    (a URL or local checkout of ACME/ABC/etc., not the bundled Hallucinate.io
    fixture) would have its fixes delivered as PRs against the wrong repo.
    Leaves `GITHUB_REPO` untouched when nothing github.com-shaped can be
    derived (e.g. a non-GitHub remote, or a local path with no `origin`),
    rather than clearing a value that may still be valid.

    Also runs `ensure_dependencies_installed()` on whatever `config.
    fixture_path()` resolves to even when `repo_arg` is falsy - previously
    only the `--repo`-resolved path got dependency-install coverage, so the
    bundled fixture path itself had no such guarantee if it were ever
    missing `node_modules` (e.g. a fresh checkout of this repo before the
    fixture's own deps had been installed).
    """
    if repo_arg:
        resolved = resolve_repo_source(repo_arg, cache_dir=config.repo_cache_dir())
        os.environ["A11Y_FIXTURE_PATH"] = str(resolved)

        github_repo = derive_github_repo(repo_arg, resolved_path=resolved)
        if github_repo:
            os.environ["GITHUB_REPO"] = github_repo
            print(f"target GitHub repo (for PR delivery): {github_repo}")  # noqa: T201
        else:
            print(  # noqa: T201
                "target GitHub repo: could not derive from --repo "
                f"(GITHUB_REPO stays {os.environ.get('GITHUB_REPO') or 'unset'})"
            )
    else:
        ensure_dependencies_installed(config.fixture_path())
    print(f"target repo: {config.fixture_path()}")  # noqa: T201


def _cmd_audit(args: argparse.Namespace) -> int:
    if not (args.repo or args.url):
        print(  # noqa: T201
            "one of --repo or --url is required: audit needs an explicit "
            "target to scan - a local path/git URL via --repo, or a live "
            "site via --url."
        )
        return 2

    if args.url:
        report = asyncio.run(_audit_live_url(args.url))
    else:
        from a11y_fixer.agents import (
            audit_crawler,
        )  # noqa: PLC0415 - deferred: avoid MCP/network cost for --help etc.

        _apply_repo_override(args.repo)
        runner = AxeAuditRunner(fixture_path=config.fixture_path())
        report = asyncio.run(audit_crawler.discover_and_audit(runner))
    output_path = config.agent_root() / DEFAULT_AUDIT_OUTPUT
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(  # noqa: T201 - CLI output
        f"wrote {report['total_violation_instances']} violation instances "
        f"across {len(report['pages'])} pages to {output_path}"
    )
    return 0


async def _audit_live_url(url: str) -> dict:
    """Audit-only path for `--url`: no clone, no build, no `ng serve` - just
    discover real routes at the given live URL and scan them directly.

    Falls back to auditing just the one given URL if discovery finds
    nothing - there's no app-agnostic default route list to fall back to
    for an arbitrary external site.
    """
    from a11y_fixer.agents import (
        audit_crawler,
    )  # noqa: PLC0415 - deferred: avoid MCP/network cost for --help etc.

    routes = await audit_crawler.discover_routes(url)
    urls = [urljoin(url, route) for route in routes] if routes else [url]
    runner = AxeAuditRunner(fixture_path=config.repo_root())
    return runner.audit_urls(urls)


async def resolve_interrupts(
    graph: CompiledStateGraph,
    thread_config: dict[str, Any],
    result: dict[str, Any],
    *,
    auto_approve: bool,
) -> dict[str, Any]:
    """Loop resolving HITL interrupts (see `HumanInTheLoopMiddleware`'s
    `HITLRequest`/`HITLResponse` contract) until the graph run completes.

    Async because the graph's MCP-backed tools are coroutine-only (`langchain-
    mcp-adapters` wraps every MCP tool call in `StructuredTool(coroutine=...)`
    with no sync `func=`) - resuming via sync `graph.invoke()` raises
    `NotImplementedError: StructuredTool does not support sync invocation`
    the moment a subagent actually calls one for real.
    """
    while result.get("__interrupt__"):
        pending = result["__interrupt__"][0].value
        action_requests = pending.get("action_requests", [])
        decisions = []
        for action in action_requests:
            if auto_approve:
                decisions.append({"type": "approve"})
                continue
            print(
                f"HITL approval needed: {action.get('description', action)}"
            )  # noqa: T201
            answer = input("Approve? [y/N] ").strip().lower()
            decisions.append(
                {"type": "approve"}
                if answer == "y"
                else {"type": "reject", "message": "rejected by reviewer"}
            )
        result = await graph.ainvoke(
            Command(resume={"decisions": decisions}), config=thread_config
        )
    return result


def _hitl_queue_path(violation: dict) -> Path:
    slug = f"{violation['rule']}-{violation['selector']}"
    slug = "".join(ch if ch.isalnum() else "-" for ch in slug).strip("-") or "violation"
    return config.hitl_queue_dir() / f"{time.time_ns()}-{slug}.json"


def _capture_and_reset_git_changes(fixture: Path) -> list[pr_delivery.FileChange]:
    """Diff the fixture's working tree against HEAD, then reset it.

    Each violation gets its own PR/dry-run branch, so the working tree must
    return to a clean baseline before the next violation runs - otherwise
    violations would contaminate each other's diffs.
    """
    status = subprocess.run(  # noqa: S603, S607
        ["git", "status", "--porcelain"],
        cwd=fixture,
        capture_output=True,
        text=True,
        check=True,
    )
    changed_paths = [line[3:] for line in status.stdout.splitlines() if line.strip()]

    changes = []
    for rel_path in changed_paths:
        file_path = fixture / rel_path
        new_content = (
            file_path.read_text(encoding="utf-8") if file_path.exists() else ""
        )
        old_result = subprocess.run(  # noqa: S603, S607
            ["git", "show", f"HEAD:{rel_path}"],
            cwd=fixture,
            capture_output=True,
            text=True,
            check=False,
        )
        old_content = old_result.stdout if old_result.returncode == 0 else ""
        changes.append(
            pr_delivery.FileChange(
                path=rel_path, old_content=old_content, new_content=new_content
            )
        )

    subprocess.run(
        ["git", "checkout", "--", "."],
        cwd=fixture,
        capture_output=True,
        text=True,
        check=False,
    )  # noqa: S603, S607
    subprocess.run(
        ["git", "clean", "-fd"],
        cwd=fixture,
        capture_output=True,
        text=True,
        check=False,
    )  # noqa: S603, S607
    return changes


def deliver_violation(
    violation: dict,
    response: ViolationResponse,
    *,
    fixture: Path,
    pr_config: config.PRDeliveryConfig,
    output_dir: Path,
    p_ik_floor: float | None = None,
    verified_deterministic: bool = False,
) -> dict:
    """Route one resolved violation to the human queue or PR delivery.

    Captures (and resets) the fixture's git diff unconditionally, before the
    route is decided - codebase_compiler's writes happen during graph
    invocation, before this function is ever called, so the human path must
    reset the working tree too or a queued violation's diff would
    contaminate the next violation.

    Args:
        p_ik_floor: Calibrated P(IK) floor from Phase 2/4 calibration. If provided,
                   overrides the default threshold for HITL escalation. Phase 3-4 feature.
        verified_deterministic: Forwarded to `assess_risk()` - set only by
            the html-has-lang fast-track call site (see that parameter's
            docstring). Never set for LLM-authored fixes.
    """
    changes = _capture_and_reset_git_changes(fixture)
    no_changes = not changes
    if no_changes:
        # Don't bail out yet: an empty diff (agent produced no edits, or a
        # race reset the fixture before this ran) must still be able to
        # escalate to a human. Bailing here unconditionally silently drops
        # the escalation - see case-10 (2026-09-03), where route was
        # correctly "human" but no HITL queue entry was ever written because
        # this function returned before reaching the queue-write logic.
        print(  # noqa: T201
            f"  \u26a0\ufe0f  deliver_violation: no file changes captured for "
            f"{violation.get('rule')} ({violation.get('selector')}); "
            f"response.route={response.route!r} - still evaluating for HITL escalation"
        )

    path_violations = []
    for change in changes:
        _, reason = validate_write_path(change.path, root=fixture)
        if reason is not None:
            path_violations.append(f"{change.path}: {reason}")

    p_ik = max(0.0, min(1.0, response.score / 20.0))
    gate = epistemic_gate(p_ik)
    # Use calibrated floor if provided (Phase 4), otherwise defaults in assess_risk()
    assess_risk_kwargs = {
        "rule": violation["rule"],
        "rubric_score": response.score,
        "p_ik": p_ik,
    }
    if p_ik_floor is not None:
        assess_risk_kwargs["p_ik_floor"] = p_ik_floor
    if verified_deterministic:
        assess_risk_kwargs["verified_deterministic"] = True
    # No captured changes means no per-file path to run the blast-radius
    # check against - fall back to a single file_path="" assessment so the
    # rule-level (high_risk_rule) and confidence-level (low_confidence)
    # guardrails still fire; only high_blast_radius is necessarily False.
    assessments = [
        assess_risk(
            file_path=change.path,
            **assess_risk_kwargs,
        )
        for change in changes
    ] or [assess_risk(file_path="", **assess_risk_kwargs)]
    # Every guardrail signal may only escalate the model's own call, never downgrade it.
    route = (
        "human"
        if response.route == "human"
        or path_violations
        or gate["verdict"] == "BLOCK"
        or any(a.route == "human" for a in assessments)
        else "auto"
    )

    if no_changes and route == "auto":
        # Genuinely nothing to do: no diff to deliver and no escalation needed.
        return {
            "delivered": False,
            "reason": "codebase_compiler made no file changes",
            "route": route,
        }

    if route == "human":
        config.hitl_queue_dir().mkdir(parents=True, exist_ok=True)

        # NEW: Deduplication gate for HITL queue (Phase 3 feature)
        store = ViolationStore(
            status_file=config.agent_root() / ".violation_status.json"
        )
        queue_gate = HITLQueueGate(store)

        # Determine action: ADD, SKIP, or REPLACE
        action, reason, old_queue_path = queue_gate.should_queue(
            rule_id=violation["rule"],
            selector=violation["selector"],
            score=response.score,
        )

        if action == "SKIP":
            # Already queued with adequate or better solution
            return {
                "delivered": False,
                "reason": f"hitl_queue_dedup: {reason}",
                "route": route,
            }

        # ADD or REPLACE: Write new queue entry
        queue_path = _hitl_queue_path(violation)
        queue_path.write_text(
            json.dumps(
                {
                    "violation": violation,
                    "response": response.model_dump(),
                    "risk_assessments": [vars(a) for a in assessments],
                    "epistemic_gate": gate,
                    "path_violations": path_violations,
                    "changes": [vars(change) for change in changes],
                    "no_file_changes": no_changes,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        # Record in violation store
        queue_gate.record_queue_entry(
            rule_id=violation["rule"],
            selector=violation["selector"],
            queue_path=str(queue_path),
            score=response.score,
        )

        # Clean up old queue entry if replacing
        if action == "REPLACE" and old_queue_path:
            old_path = Path(old_queue_path)
            if old_path.exists():
                old_path.unlink()  # Delete old file

        return {
            "delivered": False,
            "queue_path": str(queue_path),
            "route": route,
            "queue_action": action,
        }

    # NEW: Task 2.1-2.3 - Compute violation ID and solution hash for tracking
    violation_id = compute_violation_id(violation["rule"], violation["selector"])
    solution_hash = hashlib.sha256(response.code.encode("utf-8")).hexdigest()[:12]

    # NEW: Task 2.2 - Build PR title with violation ID tag
    title = f"a11y-fixer: fix {violation['rule']} ({violation['selector']}) [violation-{violation_id}]"

    # NEW: Task 2.3 - Build branch name with violation ID
    branch_name = f"a11y-fixer/{violation_id}"

    # NEW: Task 2.3 - Enhance PR body with violation metadata
    body = (
        f"{response.rationale}\n\n"
        f"---\n\n"
        f"**Violation Metadata** (Phase 0.2 Tracking)\n"
        f"- Violation ID: `{violation_id}`\n"
        f"- Rule: {violation['rule']}\n"
        f"- Selector: `{violation['selector']}`\n"
        f"- Solution Hash: `{solution_hash}`\n"
        f"- Score: {response.score}/20\n"
    )

    plan = pr_delivery.PullRequestPlan(
        title=title,
        body=body,
        branch_name=branch_name,
        changes=changes,
    )
    result = pr_delivery.deliver(plan, config=pr_config, output_dir=output_dir)

    # NEW: Task 3.2-3.4 - Auto-merge and dedup cleanup (only in live mode)
    if pr_config.github_token and result and isinstance(result, pr_delivery.LiveResult):
        # Task 3.2: Attempt auto-merge if score is high enough
        pr_number = result.pull_request_number
        auto_merge_threshold = 18.0
        if response.score >= auto_merge_threshold:
            try:
                pr_mgr = GitHubPRManager(
                    github_token=pr_config.github_token,
                    github_repo=pr_config.github_repo,
                )
                merge_result = pr_mgr.auto_merge_pr(
                    pr_number, response.score, merge_threshold=auto_merge_threshold
                )

                # Bug fix (2026-09-04): auto_merge_pr() returns a PRMergeResult
                # dataclass (success/pr_number/reason), not a dict. The old
                # `merge_result.get("merged")` raised AttributeError on every
                # call - success or failure - silently caught below and
                # misreported as "Auto-merge/cleanup failed" even when the
                # merge genuinely succeeded. That also made
                # cleanup_duplicate_prs() unreachable dead code, which is why
                # closed-but-unmerged duplicate PRs accumulated (18 on the
                # image-alt rule alone) instead of being cleaned up.
                # hitl/review_queue.py's independent auto-merge call site
                # already handled this correctly (`if merge_result.success:`)
                # - this mirrors that proven pattern.
                if merge_result.success:
                    print(
                        f"  ✅ Auto-merged PR {pr_number}: {merge_result.reason}"
                    )  # noqa: T201

                    # Task 3.3: Close duplicate PRs if merge succeeded.
                    # cleanup_duplicate_prs() returns list[PRCloseResult], not a dict.
                    dup_results = pr_mgr.cleanup_duplicate_prs(
                        violation_id, kept_pr_number=pr_number
                    )
                    closed = [dup for dup in dup_results if dup.success]
                    if closed:
                        print(f"  🧹 Closed {len(closed)} duplicate PRs")  # noqa: T201
                else:
                    print(
                        f"  ⏸️  Auto-merge not completed for PR {pr_number}: {merge_result.reason}"
                    )  # noqa: T201
            except Exception as exc:  # noqa: BLE001
                print(f"  ⚠️  Auto-merge/cleanup failed: {exc}")  # noqa: T201
        else:
            print(
                f"  ⏸️  Score {response.score} < threshold {auto_merge_threshold}, PR held for review"
            )  # noqa: T201

    return {"delivered": True, "result": result, "route": route}


def warn_on_overconfidence(context: str, rationale: str) -> None:
    """Print a warning if `rationale` trips the overconfidence-marker scanner.

    Shared by the live CLI and the offline eval harness so both surfaces flag
    the same fabrication-prone language, not just build/test/route failures.
    """
    calibration = check_confidence_calibration(rationale)
    if calibration["verdict"] == "PASS":
        return
    markers = ", ".join(
        marker for marker, _context, _alt in calibration["flagged_phrases"]
    )
    print(
        f"[{context}] confidence-calibration {calibration['verdict']} (markers: {markers})"
    )  # noqa: T201


def _filter_violations_by_case_ids(
    violations: list[dict], case_ids: str | None
) -> list[dict]:
    """Filter violations to only those matching benchmark case IDs (if provided).

    If `--case-ids case-01,case-03` is given, loads benchmark_cases.json and
    filters violations to only those in those cases. Returns all violations
    if case_ids is None.
    """
    if case_ids is None:
        return violations

    # Load benchmark cases and build a set of (rule, page, selector) tuples to match
    benchmark_path = config.agent_root() / "evaluation" / "benchmark_cases.json"
    if not benchmark_path.exists():
        print(
            f"warning: benchmark_cases.json not found at {benchmark_path}; skipping case filtering"
        )  # noqa: T201
        return violations

    try:
        benchmark_cases = json.loads(benchmark_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(
            f"warning: failed to load benchmark_cases.json: {exc}; skipping case filtering"
        )  # noqa: T201
        return violations

    requested_case_ids = {c.strip() for c in case_ids.split(",")}
    target_specs = set()
    for case in benchmark_cases:
        if case.get("id") in requested_case_ids:
            target_specs.add((case.get("rule"), case.get("page"), case.get("selector")))

    if not target_specs:
        print(
            f"warning: no benchmark cases matched {requested_case_ids}; returning all violations"
        )  # noqa: T201
        return violations

    filtered = [
        v
        for v in violations
        if (
            v.get("rule"),
            urlparse(v.get("url", "")).path or "/",
            v.get("selector"),
        )
        in target_specs
    ]
    print(
        f"filtered {len(violations)} violations → {len(filtered)} matching case IDs {requested_case_ids}"
    )  # noqa: T201
    return filtered


async def _acmd_run(args: argparse.Namespace) -> int:
    from a11y_fixer.agents import (
        audit_crawler,
    )  # noqa: PLC0415 - deferred: avoid MCP/network cost for --help etc.
    from a11y_fixer.deep_agent import (
        abuild_agent,
    )  # noqa: PLC0415 - deferred: avoid MCP/network cost for --help etc.

    if not (args.repo or args.url or args.audit):
        # No implicit default target anymore - the caller must say what to
        # fix: a repo/URL to crawl fresh, or a saved report to replay.
        print(  # noqa: T201
            "one of --repo, --url, or --audit is required: run needs an "
            "explicit target - a repo checkout/URL to fix, a live URL to "
            "audit (alongside --repo), or a saved audit report to replay."
        )
        return 2

    if args.url and not args.repo:
        # A live URL alone gives the pipeline nothing to write fixes into
        # and no repo to open PRs against - `audit --url` covers the
        # report-only case; `run` needs both.
        print(  # noqa: T201
            "--url requires --repo: fixes need a source checkout to write to "
            "and a repo to open PRs against. Use `audit --url` on its own if "
            "you only want a report."
        )
        return 2

    _apply_repo_override(args.repo)

    if args.audit:
        report = json.loads(Path(args.audit).read_text(encoding="utf-8"))
        validation_error = validate_raw_axe_reports(report.get("raw_reports", []))
        if validation_error is not None:
            print(
                f"invalid audit report {args.audit}: {validation_error}"
            )  # noqa: T201
            return 2
    elif args.url:
        # Audit the live, deployed site directly (no clone/build/serve) while
        # still fixing --repo's actual source and delivering PRs against it -
        # useful when the live app differs from a plain local `ng serve`
        # (SSR output, prod config, etc.) but you still want fixes upstream.
        report = await _audit_live_url(args.url)
    else:
        report = await audit_crawler.discover_and_audit(
            AxeAuditRunner(fixture_path=config.fixture_path())
        )

    violations = flatten_violation_instances(report)
    if not violations:
        print("no violations found - nothing to do")  # noqa: T201
        return 0

    # NEW: Filter by case IDs if --case-ids provided
    violations = _filter_violations_by_case_ids(violations, args.case_ids)
    if not violations:
        print("no violations after filtering by case IDs")  # noqa: T201
        return 0

    pr_config = config.resolve_pr_delivery(args.live)
    fixture = config.fixture_path()
    output_dir = config.agent_root() / "evaluation" / "results" / "prs"

    # NEW: Initialize Phase 0.2 violation tracking
    store = ViolationStore(status_file=config.agent_root() / ".violation_status.json")
    gate = PrePipelineGate(store)
    metrics = {
        "processed": 0,
        "skipped": 0,
        "created": 0,
        "replaced": 0,
    }

    # FIX 2: Calibrate P(IK) floor from Phase 3 results (Phase 4 feature)
    # Dynamically computes calibrated floor from evaluation results if available
    p_ik_floor = None
    results_summary_path = (
        config.agent_root() / "evaluation" / "results" / "results_summary.json"
    )
    if results_summary_path.exists():
        try:
            # First, try to load pre-computed calibration (if it exists)
            results_data = json.loads(results_summary_path.read_text(encoding="utf-8"))
            p_ik_floor = results_data.get("calibrated_p_ik_floor")
        except Exception:  # noqa: BLE001
            pass  # Ignore if results file malformed

    # Phase 4: If no pre-computed calibration, compute it from phase results
    if p_ik_floor is None:
        # Try full Phase 3 results first (results_phase_all.json)
        phase_results_path = (
            config.agent_root() / "evaluation" / "results" / "results_phase_all.json"
        )
        if phase_results_path.exists():
            try:
                calibration = calibrate_from_results(
                    phase_results_path, target_fpr=0.05
                )
                if calibration.calibrated:
                    p_ik_floor = calibration.p_ik_floor
                    print(
                        f"📊 Phase 4 Calibration: Computed P(IK) floor = {calibration.p_ik_floor:.3f} "
                        f"(AUC={calibration.auc:.3f}, n={calibration.sample_size})"
                    )  # noqa: T201
                else:
                    print(
                        f"📊 Phase 4 Calibration: Insufficient data to calibrate "
                        f"(n={calibration.sample_size}, using default floor)"
                    )  # noqa: T201
            except Exception as e:  # noqa: BLE001
                print(f"⚠️  Calibration failed: {e}")  # noqa: T201

    graph = await abuild_agent()

    failures = 0
    for index, violation in enumerate(violations):
        # NEW: Check PrePipelineGate FIRST
        violation_id = compute_violation_id(violation["rule"], violation["selector"])
        action, reason, old_pr_number = gate.should_process(
            rule_id=violation["rule"],
            selector=violation["selector"],
            new_score=None,  # Will update after qa_critic
            new_solution_hash=None,  # Will update after codebase_compiler
        )

        if action == "SKIP":
            print(f"⏭️  Skipping {violation_id}: {reason}")  # noqa: T201
            metrics["skipped"] += 1
            # NEW: Task 3.4 - Persist skip decision (maintains state across runs)
            status = store.get(violation_id)
            if status:
                # Update existing status to reflect it's still valid but skipped this run
                status = status
            else:
                # Create new status for this violation
                status = ViolationStatus(
                    violation_id=violation_id,
                    rule_id=violation["rule"],
                    selector=violation["selector"],
                    current_pr_number=None,
                    current_score=None,
                    current_solution_hash=None,
                    state=ViolationState.NEW,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            store.upsert(status)
            continue

        # action == "CREATE" or "REPLACE" — check for html-lang fast-track
        if is_html_lang_violation(violation):
            print(
                f"🎯 [HTML-LANG] {violation_id}: Fast-track html-has-lang fix"
            )  # noqa: T201
            html_lang_fix = get_html_lang_fix()
            apply_result = await apply_html_lang(fixture)

            if apply_result["applied"]:
                print("  ✅ Applied & verified (ng build passed)")  # noqa: T201

                # Create ViolationResponse for delivery
                from a11y_fixer.deep_agent import ViolationResponse  # noqa: PLC0415

                html_lang_response = ViolationResponse(
                    rule=violation["rule"],
                    wcag=html_lang_fix.wcag_sc,
                    selector=violation["selector"],
                    technique_id=html_lang_fix.technique_id,
                    technique_type="sufficient",
                    code=html_lang_fix.template_code,
                    rationale=html_lang_fix.rationale,
                    score=20.0,  # Deterministic, build-verified
                    route="auto",  # Auto-merge (bypasses HITL)
                )

                try:
                    delivered = deliver_violation(
                        violation,
                        html_lang_response,
                        fixture=fixture,
                        pr_config=pr_config,
                        output_dir=output_dir,
                        p_ik_floor=p_ik_floor,
                        verified_deterministic=True,
                    )
                    print(
                        f"  📤 Delivered: {delivered['route']} (auto-merge PR)"
                    )  # noqa: T201

                    # Record as MERGED in violation store for deduplication.
                    # deliver_violation() only ever returns a bare "pr_number"
                    # key on its (never-taken, pre-2026-09) human-route dict;
                    # a real PR number for the auto route lives on its
                    # LiveResult under "result" instead.
                    pr_number = delivered.get("pr_number")
                    delivery_result = delivered.get("result")
                    if pr_number is None and isinstance(
                        delivery_result, pr_delivery.LiveResult
                    ):
                        pr_number = delivery_result.pull_request_number
                    store.mark_merged(
                        violation_id=violation_id,
                        rule_id=violation["rule"],
                        selector=violation["selector"],
                        pr_number=pr_number,
                    )
                    # mark_merged() only updates the in-memory cache (the
                    # same contract hitl/review_queue.py's own call site
                    # follows, saving right after) - without this, the
                    # MERGED status never reaches .violation_status.json
                    # and PrePipelineGate treats this violation as brand
                    # new again on every subsequent run.
                    store.save()

                    metrics["created"] += 1
                except Exception as e:  # noqa: BLE001
                    print(f"  ❌ Delivery failed: {e}")  # noqa: T201
                    failures += 1

                continue  # Skip full pipeline, move to next violation
            else:
                print(
                    f"  ⚠️  Fast-track failed: {apply_result.get('error')}"
                )  # noqa: T201
                print("  🔄 Falling back to full pipeline...")  # noqa: T201
                # Fall through to normal pipeline

        # Proceed with full pipeline (either not html-lang, or html-lang fast-track failed)
        print(f"▶️  Processing {violation_id}: {action}")  # noqa: T201
        metrics["created"] += 1

        message = (
            "Resolve this axe-core violation:\n"
            f"rule: {violation['rule']}\n"
            f"page: {violation['url']}\n"
            f"selector: {violation['selector']}\n"
            f"html: {violation['html']}\n"
            f"failure_summary: {violation.get('failure_summary')}\n"
        )

        # Retry up to 3 times for non-deterministic empty responses
        MAX_ATTEMPTS = 3
        violation_succeeded = False
        for attempt in range(MAX_ATTEMPTS):
            thread_config = {
                "configurable": {
                    "thread_id": f"violation-{index}-attempt{attempt + 1}",
                    "recursion_limit": 50,
                }
            }
            try:
                result = await graph.ainvoke(
                    {"messages": [{"role": "user", "content": message}]},
                    config=thread_config,
                )
                result = await resolve_interrupts(
                    graph, thread_config, result, auto_approve=args.yes
                )

                response = result.get("structured_response")
                if response is None:
                    if attempt < MAX_ATTEMPTS - 1:
                        continue  # Try again with fresh thread_id
                    # All retries exhausted
                    print(
                        f"[{violation['rule']}] no structured response produced after retries - skipping"
                    )  # noqa: T201
                    break

                # Successfully got a response
                warn_on_overconfidence(violation["rule"], response.rationale)
                outcome = deliver_violation(
                    violation,
                    response,
                    fixture=fixture,
                    pr_config=pr_config,
                    output_dir=output_dir,
                    p_ik_floor=p_ik_floor,
                )
                print(f"[{violation['rule']}] {outcome}")  # noqa: T201

                # NEW: Task 3.4 - Persist delivery result to violation store
                if outcome.get("delivered"):
                    result = outcome.get("result")
                    pr_number = None
                    if isinstance(result, pr_delivery.LiveResult):
                        pr_number = result.pull_request_number

                    solution_hash = hashlib.sha256(
                        response.code.encode("utf-8")
                    ).hexdigest()[:12]
                    status = ViolationStatus(
                        violation_id=violation_id,
                        rule_id=violation["rule"],
                        selector=violation["selector"],
                        current_pr_number=pr_number,
                        current_score=response.score,
                        current_solution_hash=solution_hash,
                        state=(
                            ViolationState.MERGED
                            if (pr_config.github_token and response.score >= 18.0)
                            else ViolationState.PR_OPEN
                        ),
                        created_at=datetime.now(UTC),
                        updated_at=datetime.now(UTC),
                    )
                    store.upsert(status)
                    print(
                        f"  💾 Persisted {violation_id} (PR#{pr_number}, score {response.score}/20)"
                    )  # noqa: T201

                violation_succeeded = True
                break
            except (
                Exception
            ) as exc:  # noqa: BLE001 - one violation's failure must not abort the rest of the batch
                # Don't retry on exceptions, just skip and move to next violation
                failures += 1
                print(
                    f"[{violation['rule']}] FAILED: {type(exc).__name__}: {exc}"
                )  # noqa: T201
                break

        if not violation_succeeded and attempt == MAX_ATTEMPTS - 1 and response is None:
            # Explicitly mark as failed if all retries exhausted with no response
            failures += 1

    # NEW: Print Phase 0.2 metrics at end
    print("\n" + "=" * 50)  # noqa: T201
    print(f"=== Phase 0.2 Violation Tracking Metrics ===")  # noqa: T201
    print(f"Total violations in audit: {len(violations)}")  # noqa: T201
    print(f"Skipped (duplicates prevented): {metrics['skipped']}")  # noqa: T201
    print(f"Created (new violations): {metrics['created']}")  # noqa: T201
    print(f"Replaced (better solution): {metrics['replaced']}")  # noqa: T201
    print(f"Failed to resolve: {failures}")  # noqa: T201
    print("=" * 50 + "\n")  # noqa: T201

    if failures:
        print(f"{failures}/{len(violations)} violation(s) failed")  # noqa: T201
    return 1 if failures else 0


def _cmd_run(args: argparse.Namespace) -> int:
    return asyncio.run(_acmd_run(args))


def _cmd_review(args: argparse.Namespace) -> int:
    from a11y_fixer.hitl.review_queue import (
        ReviewQueue,
    )  # noqa: PLC0415 - deferred: keeps --help free of extra imports

    pr_config = config.resolve_pr_delivery(args.live)
    output_dir = config.agent_root() / "evaluation" / "results" / "prs"
    queue = ReviewQueue(
        config.hitl_queue_dir(),
        wiki_dir=config.wiki_dir(),
        pr_config=pr_config,
        output_dir=output_dir,
    )

    if args.list:
        pending = queue.list_pending()
        if not pending:
            print("hitl queue is empty")  # noqa: T201
            return 0
        for path in pending:
            violation = json.loads(path.read_text(encoding="utf-8"))["violation"]
            print(
                f"{path.name}: {violation['rule']} ({violation.get('selector', '?')})"
            )  # noqa: T201
        return 0

    if not args.item or not (args.approve or args.reject):
        print(
            "usage: a11y-fixer review <item> (--approve | --reject) [--notes ...]"
        )  # noqa: T201
        return 1

    queue_path = (
        Path(args.item)
        if Path(args.item).is_absolute()
        else config.hitl_queue_dir() / args.item
    )
    if not queue_path.exists():
        print(f"not found in the hitl queue: {queue_path}")  # noqa: T201
        return 1

    decision = "approve" if args.approve else "reject"
    result = queue.review(
        queue_path, decision, reviewer=args.reviewer, notes=args.notes or ""
    )
    print(json.dumps(result, default=str, indent=2))  # noqa: T201
    return 0


def _cmd_queue_sync(args: argparse.Namespace) -> int:
    """List and manage queue items, show sync status with GitHub PRs."""
    from a11y_fixer.hitl.review_queue import (
        ReviewQueue,
    )  # noqa: PLC0415 - deferred

    pr_config = config.resolve_pr_delivery(args.live)
    output_dir = config.agent_root() / "evaluation" / "results" / "prs"
    queue = ReviewQueue(
        config.hitl_queue_dir(),
        wiki_dir=config.wiki_dir(),
        pr_config=pr_config,
        output_dir=output_dir,
    )

    # Handle --check-merged first (separate from queue listing)
    if args.check_merged:
        return _check_merged_prs(pr_config, args.live)

    pending = queue.list_pending()
    stats = queue.get_stats()

    # Show summary
    print(f"\n📊 HITL Queue Status")  # noqa: T201
    print(
        f"   Pending: {stats['pending']} | Reviewed: {stats['reviewed']} | Total: {stats['total']}"
    )

    if not pending:
        print("   ✅ Queue is empty")  # noqa: T201
        return 0

    # Parse all pending items
    items_by_score = []
    for path in pending:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            violation = data.get("violation", {})
            response = data.get("response", {})
            score = response.get("score", 0)
            rule = violation.get("rule", "unknown")
            selector = violation.get("selector", "?")
            items_by_score.append(
                {
                    "path": path,
                    "filename": path.name,
                    "score": score,
                    "rule": rule,
                    "selector": selector,
                }
            )
        except Exception as e:  # noqa: BLE001
            print(f"   ⚠️  Failed to parse {path.name}: {e}")  # noqa: T201

    items_by_score.sort(key=lambda x: x["score"], reverse=True)

    # Show detailed list
    print(f"\n📋 Pending Queue Items (sorted by score):")  # noqa: T201
    for i, item in enumerate(items_by_score, 1):
        score_emoji = (
            "🟢" if item["score"] >= 18 else "🟡" if item["score"] >= 15 else "🔴"
        )
        print(
            f"   {i}. [{score_emoji} {item['score']}/20] {item['rule']:20} | {item['selector'][:40]}"
        )  # noqa: T201
        print(f"      📄 {item['filename']}")  # noqa: T201

    # If auto-approve flag, approve high-scoring items
    if args.auto_approve:
        high_score_items = [item for item in items_by_score if item["score"] >= 18.0]
        if not high_score_items:
            print(f"\n   No items with score ≥ 18.0 to auto-approve")  # noqa: T201
            return 0

        print(
            f"\n✅ Auto-approving {len(high_score_items)} high-scoring item(s):"
        )  # noqa: T201
        for item in high_score_items:
            try:
                result = queue.review(
                    item["path"],
                    "approve",
                    reviewer="auto-sync",
                    notes="Auto-approved by queue-sync: score ≥ 18.0",
                )
                pr_num = result.get("result", {}).get("pull_request_number", "?")
                print(f"   ✓ {item['rule']:20} → PR #{pr_num}")  # noqa: T201
            except Exception as e:  # noqa: BLE001
                print(f"   ✗ {item['rule']:20} → Error: {e}")  # noqa: T201

    return 0


def _check_merged_prs(pr_config: config.PRDeliveryConfig, live: bool | None) -> int:
    """Check GitHub for merged PRs and update violation store.

    This syncs the system's view with GitHub's reality. If a PR was merged
    manually in GitHub, this command detects it and updates the violation
    store to `state = MERGED`, preventing duplicate PR creation on next audit.
    """
    import httpx

    if not pr_config.github_token:
        print("❌ GitHub token not configured (set GITHUB_TOKEN env var)")  # noqa: T201
        return 1

    store = ViolationStore(status_file=config.agent_root() / ".violation_status.json")

    if not store._cache:
        print("✅ No violations in store to check")  # noqa: T201
        return 0

    # Filter to PR_OPEN violations only
    open_prs = [
        (vid, status)
        for vid, status in store._cache.items()
        if status.state == ViolationState.PR_OPEN and status.current_pr_number
    ]

    if not open_prs:
        print("✅ No open PRs to check")  # noqa: T201
        return 0

    print(f"\n🔄 Checking GitHub for merged PRs...")  # noqa: T201
    print(f"   Total open PRs to check: {len(open_prs)}")  # noqa: T201

    merged_count = 0
    headers = {
        "Authorization": f"Bearer {pr_config.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    owner, _, repo = pr_config.github_repo.partition("/")
    base_url = "https://api.github.com"

    with httpx.Client(base_url=base_url, headers=headers, timeout=30.0) as client:
        for vid, status in open_prs:
            pr_num = status.current_pr_number
            try:
                pr_resp = client.get(f"/repos/{owner}/{repo}/pulls/{pr_num}")
                if pr_resp.status_code != 200:  # noqa: PLR2004
                    print(
                        f"   ⚠️  PR #{pr_num}: API error {pr_resp.status_code}"
                    )  # noqa: T201
                    continue

                pr_data = pr_resp.json()
                is_merged = (
                    pr_data.get("merged", False) or pr_data.get("merged_at") is not None
                )
                state = pr_data.get("state")

                if is_merged:
                    print(
                        f"   ✅ PR #{pr_num} [{status.rule_id}] is MERGED (score: {status.current_score}/20)"
                    )  # noqa: T201
                    # Update store
                    status.state = ViolationState.MERGED
                    status.updated_at = datetime.now(UTC)
                    if live:
                        store.upsert(status)
                    merged_count += 1
                else:
                    print(
                        f"   ⏳ PR #{pr_num} [{status.rule_id}] is {state.upper()} (not merged)"
                    )  # noqa: T201
            except Exception as e:  # noqa: BLE001
                print(f"   ❌ PR #{pr_num}: Error: {e}")  # noqa: T201

    if merged_count == 0:
        print(f"\n   No merged PRs found")  # noqa: T201
        return 0

    if live:
        store.save()
        print(
            f"\n✅ Updated violation store: {merged_count} PR(s) marked as MERGED"
        )  # noqa: T201
    else:
        print(
            f"\n📋 DRY-RUN: Would update {merged_count} PR(s) to MERGED state"
        )  # noqa: T201
        print(f"   Run with --live to persist changes")  # noqa: T201

    return 0


def _cmd_fleet(args: argparse.Namespace) -> int:
    """Fleet entry point: run `run` against the one site declared in a YAML
    manifest (`--config`), instead of a single `--repo` passed on the
    command line.

    Deliberately limited to exactly one site per manifest/invocation for
    now: the HITL queue, `.violation_status.json`, and audit-output paths
    (see `config.hitl_queue_dir()` et al.) are all global/unnamespaced, so
    running more than one site through the same process risks one site's
    state corrupting another's. Revisit this limit once that state is
    per-site-namespaced.

    `--live` is the default here, unlike `run` (which defaults to a safe
    dry-run) - a fleet run is meant to be unattended, so opting into a
    dry-run is the explicit choice (`--dry-run`) rather than the default.
    Because that default flips the usual safety direction, the `LIVE: ...`
    banner below is printed unconditionally before anything live happens,
    so a human skimming the output can't miss it.

    The site's GitHub token is resolved from the environment variable named
    by its `github_token_env` (default `GITHUB_TOKEN`) - never written in
    the manifest itself - and temporarily exported as `GITHUB_TOKEN` for
    the duration of the run (mirroring how `_apply_repo_override` scopes
    its own env overrides), then restored on the way out.
    """
    try:
        sites = load_manifest(args.config)
    except FleetManifestError as exc:
        print(f"fleet: {exc}")  # noqa: T201
        return 2

    if len(sites) != 1:
        print(  # noqa: T201
            "fleet currently supports one site per invocation "
            f"(manifest has {len(sites)})"
        )
        return 2

    site = sites[0]
    live = not args.dry_run

    token_env = site.github_token_env
    token_value = os.environ.get(token_env)
    if live and not token_value:
        print(  # noqa: T201
            f"fleet: {site.site_id} needs a GitHub token for live delivery - "
            f"set the {token_env} environment variable (see github_token_env "
            "in the manifest) or pass --dry-run."
        )
        return 2

    target = derive_github_repo(site.repo) or site.repo
    mode = "LIVE" if live else "dry-run"
    print(f"{mode}: {site.site_id} -> {site.repo} (PR target: {target})")  # noqa: T201
    if site.audit:
        print(f"  replaying saved audit report: {site.audit}")  # noqa: T201

    previous_token = os.environ.get("GITHUB_TOKEN")
    if token_value is not None:
        os.environ["GITHUB_TOKEN"] = token_value
    try:
        run_args = argparse.Namespace(
            repo=site.repo,
            url=site.url,
            audit=site.audit,
            case_ids=None,
            live=live,
            yes=True,
        )
        return asyncio.run(_acmd_run(run_args))
    finally:
        if previous_token is None:
            os.environ.pop("GITHUB_TOKEN", None)
        else:
            os.environ["GITHUB_TOKEN"] = previous_token


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="a11y-fixer")
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit_parser = subparsers.add_parser(
        "audit", help="Run a full axe-core audit against the fixture"
    )
    audit_target_group = audit_parser.add_mutually_exclusive_group(required=True)
    audit_target_group.add_argument(
        "--repo",
        default=None,
        help="Local path or git URL of the Angular repo to audit (required, unless --url is given)",
    )
    audit_target_group.add_argument(
        "--url",
        default=None,
        help="Audit a live, already-running site directly (no clone/build/serve) - audit-only, can't deliver fixes",
    )
    audit_parser.set_defaults(func=_cmd_audit)

    run_parser = subparsers.add_parser(
        "run", help="Run the deep agent over every violation in an audit report"
    )
    run_parser.add_argument(
        "--audit",
        default=None,
        help="Path to a normalized audit JSON; runs a fresh audit if omitted",
    )
    run_parser.add_argument(
        "--repo",
        default=None,
        help="Local path or git URL of the Angular repo to fix (required, unless --audit is given)",
    )
    run_parser.add_argument(
        "--url",
        default=None,
        help="Audit this live URL instead of building/serving --repo locally - fixes are still written "
        "to --repo and PRs opened against it, so --url requires --repo alongside it",
    )
    run_parser.add_argument(
        "--case-ids",
        default=None,
        help="Comma-separated case IDs to test (e.g. case-01,case-03,case-13); if provided, only these benchmark cases are processed",
    )
    run_parser.add_argument(
        "--yes",
        action="store_true",
        help="Auto-approve every HITL interrupt (non-interactive)",
    )
    live_group = run_parser.add_mutually_exclusive_group()
    live_group.add_argument(
        "--live",
        dest="live",
        action="store_true",
        default=None,
        help="Force live PR delivery",
    )
    live_group.add_argument(
        "--no-live", dest="live", action="store_false", help="Force dry-run delivery"
    )
    run_parser.set_defaults(func=_cmd_run)

    review_parser = subparsers.add_parser(
        "review", help="List or decide on queued HITL review items"
    )
    review_parser.add_argument(
        "item",
        nargs="?",
        default=None,
        help="Queue filename (see --list) to approve or reject",
    )
    review_parser.add_argument(
        "--list", action="store_true", help="List pending queue items and exit"
    )
    decision_group = review_parser.add_mutually_exclusive_group()
    decision_group.add_argument(
        "--approve", action="store_true", help="Approve and deliver the queued fix"
    )
    decision_group.add_argument(
        "--reject", action="store_true", help="Reject and record a wiki lesson"
    )
    review_parser.add_argument(
        "--notes",
        default=None,
        help="Constraint/reason text (used as the wiki lesson on --reject)",
    )
    review_parser.add_argument(
        "--reviewer",
        default="cli",
        help="Reviewer name recorded in the decision (default: %(default)s)",
    )
    review_live_group = review_parser.add_mutually_exclusive_group()
    review_live_group.add_argument(
        "--live",
        dest="live",
        action="store_true",
        default=None,
        help="Force live PR delivery on --approve",
    )
    review_live_group.add_argument(
        "--no-live",
        dest="live",
        action="store_false",
        help="Force dry-run delivery on --approve",
    )
    review_parser.set_defaults(func=_cmd_review)

    queue_sync_parser = subparsers.add_parser(
        "queue-sync",
        help="List pending HITL queue items and auto-approve high-scoring fixes",
    )
    queue_sync_parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="Auto-approve all items with score ≥ 18.0 and deliver as PRs",
    )
    queue_sync_parser.add_argument(
        "--check-merged",
        action="store_true",
        help="Check GitHub for manually merged PRs and update violation store",
    )
    queue_sync_parser_live_group = queue_sync_parser.add_mutually_exclusive_group()
    queue_sync_parser_live_group.add_argument(
        "--live",
        dest="live",
        action="store_true",
        default=None,
        help="Force live PR delivery for auto-approved items",
    )
    queue_sync_parser_live_group.add_argument(
        "--no-live",
        dest="live",
        action="store_false",
        help="Force dry-run delivery for auto-approved items",
    )
    queue_sync_parser.set_defaults(func=_cmd_queue_sync)

    fleet_parser = subparsers.add_parser(
        "fleet",
        help="Run `run` against the one site declared in a YAML manifest (see sites.example.yaml)",
    )
    fleet_parser.add_argument(
        "--config",
        required=True,
        help="Path to a fleet manifest YAML file (see sites.example.yaml)",
    )
    fleet_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Write diffs instead of opening PRs (fleet defaults to LIVE delivery, unlike `run`)",
    )
    fleet_parser.set_defaults(func=_cmd_fleet)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
