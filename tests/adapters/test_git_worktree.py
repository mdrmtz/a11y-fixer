from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from a11y_fixer.adapters.sandbox.git_worktree import GitWorktreeError, create_worktree, list_worktrees, remove_worktree


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A real, disposable git repo with one commit - never touches Hallucinate.io."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)  # noqa: S603, S607
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)  # noqa: S603, S607
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)  # noqa: S603, S607
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)  # noqa: S603, S607
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repo, check=True)  # noqa: S603, S607
    return repo


def test_create_worktree_produces_an_isolated_checkout(git_repo: Path, tmp_path: Path) -> None:
    worktree = create_worktree(git_repo, base_dir=tmp_path, branch_name="a11y-fixer/test-1")

    assert worktree.path.exists()
    assert (worktree.path / "README.md").read_text(encoding="utf-8") == "hello\n"

    remove_worktree(worktree)


def test_worktree_edits_never_touch_the_source_repo(git_repo: Path, tmp_path: Path) -> None:
    worktree = create_worktree(git_repo, base_dir=tmp_path, branch_name="a11y-fixer/test-2")

    (worktree.path / "README.md").write_text("modified in worktree\n", encoding="utf-8")

    assert (git_repo / "README.md").read_text(encoding="utf-8") == "hello\n"

    remove_worktree(worktree)


def test_link_dirs_symlinks_gitignored_directories(git_repo: Path, tmp_path: Path) -> None:
    node_modules = git_repo / "node_modules"
    node_modules.mkdir()
    (node_modules / "marker.txt").write_text("installed package\n", encoding="utf-8")

    worktree = create_worktree(git_repo, base_dir=tmp_path, branch_name="a11y-fixer/test-3", link_dirs=("node_modules",))

    assert (worktree.path / "node_modules" / "marker.txt").read_text(encoding="utf-8") == "installed package\n"
    assert (worktree.path / "node_modules").is_symlink()

    remove_worktree(worktree)


def test_remove_worktree_deletes_directory_and_branch(git_repo: Path, tmp_path: Path) -> None:
    worktree = create_worktree(git_repo, base_dir=tmp_path, branch_name="a11y-fixer/test-4")

    remove_worktree(worktree)

    assert not worktree.path.exists()
    branches = subprocess.run(  # noqa: S603, S607
        ["git", "branch", "--list", worktree.branch], cwd=git_repo, capture_output=True, text=True, check=True
    )
    assert branches.stdout.strip() == ""


def test_list_worktrees_includes_created_worktree(git_repo: Path, tmp_path: Path) -> None:
    worktree = create_worktree(git_repo, base_dir=tmp_path, branch_name="a11y-fixer/test-5")

    worktrees = list_worktrees(git_repo)

    paths = {Path(w["worktree"]).resolve() for w in worktrees if "worktree" in w}
    assert worktree.path.resolve() in paths

    remove_worktree(worktree)


def test_non_git_directory_raises(tmp_path: Path) -> None:
    not_a_repo = tmp_path / "just-a-folder"
    not_a_repo.mkdir()
    with pytest.raises(GitWorktreeError):
        create_worktree(not_a_repo)
