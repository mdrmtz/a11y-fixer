"""GitHub PR delivery: token-aware live/dry-run adapter.

Deviates from the plan's `@modelcontextprotocol/server-github` MCP mechanism:
talks to the GitHub REST API directly via `httpx` instead. Wiring an MCP
server for a single procedural call (rather than letting an LLM drive its own
tool calls) adds a subprocess/protocol layer with no corresponding benefit
here - the REST call is simpler, synchronous, and trivially mockable in tests.

Dry-run (default unless `GITHUB_TOKEN` is set - see
`config.resolve_pr_delivery`) writes a unified diff + markdown PR description
to disk. Live mode opens a real branch, commits, and pull request via the
GitHub REST API.
"""

from __future__ import annotations

import base64
import difflib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from a11y_fixer.config import PRDeliveryConfig

GITHUB_API_BASE = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"


class PRDeliveryError(RuntimeError):
    """Raised when a live PR delivery operation fails."""


@dataclass(frozen=True)
class FileChange:
    path: str
    old_content: str
    new_content: str


@dataclass(frozen=True)
class PullRequestPlan:
    """Everything needed to describe a fix, whether delivered live or dry-run."""

    title: str
    body: str
    branch_name: str
    changes: list[FileChange]


@dataclass(frozen=True)
class DryRunResult:
    diff_path: Path
    description_path: Path
    unified_diff: str


@dataclass(frozen=True)
class LiveResult:
    pull_request_url: str
    pull_request_number: int
    branch_name: str


def render_unified_diff(changes: list[FileChange]) -> str:
    """Render a single unified diff covering every file change in the plan."""
    parts = []
    for change in changes:
        diff = difflib.unified_diff(
            change.old_content.splitlines(keepends=True),
            change.new_content.splitlines(keepends=True),
            fromfile=f"a/{change.path}",
            tofile=f"b/{change.path}",
        )
        parts.append("".join(diff))
    return "\n".join(parts)


def deliver_dry_run(plan: PullRequestPlan, *, output_dir: Path) -> DryRunResult:
    """Write a unified diff + markdown PR description to disk.

    The safe default for unattended GitHub Action runs.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    slug = plan.branch_name.replace("/", "-")
    diff_path = output_dir / f"{timestamp}-{slug}.diff"
    description_path = output_dir / f"{timestamp}-{slug}.md"

    unified_diff = render_unified_diff(plan.changes)
    diff_path.write_text(unified_diff, encoding="utf-8")
    description_path.write_text(f"# {plan.title}\n\n{plan.body}\n", encoding="utf-8")

    return DryRunResult(diff_path=diff_path, description_path=description_path, unified_diff=unified_diff)


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.status_code >= 400:  # noqa: PLR2004
        msg = f"GitHub API error {resp.status_code} for {resp.request.method} {resp.request.url}: {resp.text[:500]}"
        raise PRDeliveryError(msg)


def _commit_file(client: httpx.Client, owner: str, repo: str, branch: str, change: FileChange) -> None:
    existing_sha = None
    resp = client.get(f"/repos/{owner}/{repo}/contents/{change.path}", params={"ref": branch})
    if resp.status_code == 200:  # noqa: PLR2004
        existing_sha = resp.json()["sha"]

    payload = {
        "message": f"a11y-fixer: update {change.path}",
        "content": base64.b64encode(change.new_content.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if existing_sha:
        payload["sha"] = existing_sha

    put_resp = client.put(f"/repos/{owner}/{repo}/contents/{change.path}", json=payload)
    _raise_for_status(put_resp)


def deliver_live(
    plan: PullRequestPlan,
    *,
    github_token: str,
    github_repo: str,
    base_branch: str = "main",
    client: httpx.Client | None = None,
) -> LiveResult:
    """Open a real branch, commit every change, and open a pull request."""
    owner, _, repo = github_repo.partition("/")
    if not owner or not repo:
        msg = f"github_repo must be 'owner/repo', got {github_repo!r}"
        raise PRDeliveryError(msg)

    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
    }
    owned_client = client is None
    http_client = client or httpx.Client(base_url=GITHUB_API_BASE, headers=headers, timeout=30.0)
    try:
        base_ref_resp = http_client.get(f"/repos/{owner}/{repo}/git/ref/heads/{base_branch}")
        _raise_for_status(base_ref_resp)
        base_sha = base_ref_resp.json()["object"]["sha"]

        create_branch_resp = http_client.post(
            f"/repos/{owner}/{repo}/git/refs",
            json={"ref": f"refs/heads/{plan.branch_name}", "sha": base_sha},
        )
        _raise_for_status(create_branch_resp)

        for change in plan.changes:
            _commit_file(http_client, owner, repo, plan.branch_name, change)

        pr_resp = http_client.post(
            f"/repos/{owner}/{repo}/pulls",
            json={"title": plan.title, "body": plan.body, "head": plan.branch_name, "base": base_branch},
        )
        _raise_for_status(pr_resp)
        pr = pr_resp.json()
        return LiveResult(pull_request_url=pr["html_url"], pull_request_number=pr["number"], branch_name=plan.branch_name)
    finally:
        if owned_client:
            http_client.close()


def deliver(
    plan: PullRequestPlan,
    *,
    config: PRDeliveryConfig,
    output_dir: Path,
    base_branch: str = "main",
) -> DryRunResult | LiveResult:
    """Dispatch to dry-run or live delivery per `config.live`."""
    if not config.live:
        return deliver_dry_run(plan, output_dir=output_dir)
    if not config.github_token or not config.github_repo:
        msg = "live delivery requires both github_token and github_repo to be set"
        raise PRDeliveryError(msg)
    return deliver_live(plan, github_token=config.github_token, github_repo=config.github_repo, base_branch=base_branch)
