"""Git-worktree isolation for candidate fix generation.

Each Tree-of-Thought node evaluation gets its own isolated worktree so a
failed candidate never bleeds into the parent branch. Plain Python tool
functions, not a bespoke MCP server - this is internal, single-consumer
machinery, so MCP protocol overhead is unjustified.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path


class GitWorktreeError(RuntimeError):
    """Raised when a git worktree operation fails."""


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)  # noqa: S603, S607
    if result.returncode != 0:
        msg = f"git {' '.join(args)} failed (exit {result.returncode}):\n{result.stderr}"
        raise GitWorktreeError(msg)
    return result


@dataclass(frozen=True)
class Worktree:
    path: Path
    branch: str
    repo_path: Path


def create_worktree(
    repo_path: Path,
    *,
    base_dir: Path | None = None,
    branch_name: str | None = None,
    link_dirs: tuple[str, ...] = (),
) -> Worktree:
    """Create an isolated git worktree off `repo_path`'s current HEAD.

    `git worktree add` does not carry over gitignored directories (e.g.
    `node_modules`); pass `link_dirs` to symlink them in from the source repo
    so tooling like `ng build` still resolves its installed packages.
    """
    repo_path = repo_path.resolve()
    base_dir = (base_dir or repo_path.parent).resolve()
    branch_name = branch_name or f"a11y-fixer/{uuid.uuid4().hex[:12]}"
    worktree_path = base_dir / branch_name.replace("/", "-")

    _run_git(["worktree", "add", "-b", branch_name, str(worktree_path), "HEAD"], cwd=repo_path)

    for dirname in link_dirs:
        source = repo_path / dirname
        if source.exists():
            (worktree_path / dirname).symlink_to(source, target_is_directory=True)

    return Worktree(path=worktree_path, branch=branch_name, repo_path=repo_path)


def remove_worktree(worktree: Worktree, *, force: bool = True) -> None:
    """Remove the worktree and delete its branch - full teardown, no bleed."""
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(worktree.path))
    try:
        _run_git(args, cwd=worktree.repo_path)
    except GitWorktreeError:
        # Worktree admin state got out of sync (e.g. dir already deleted) -
        # fall back to a filesystem removal plus an explicit prune.
        shutil.rmtree(worktree.path, ignore_errors=True)
        _run_git(["worktree", "prune"], cwd=worktree.repo_path)
    _run_git(["branch", "-D", worktree.branch], cwd=worktree.repo_path)


def list_worktrees(repo_path: Path) -> list[dict[str, str]]:
    """Parse `git worktree list --porcelain` into structured records."""
    result = _run_git(["worktree", "list", "--porcelain"], cwd=repo_path)
    worktrees: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if not line:
            if current:
                worktrees.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value
    if current:
        worktrees.append(current)
    return worktrees
