from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from a11y_fixer.adapters import repo_source
from a11y_fixer.adapters.repo_source import (
    RepoSourceError,
    derive_github_repo,
    is_url,
    resolve_repo_source,
)


@pytest.mark.parametrize(
    "source",
    [
        "https://github.com/mdrmtz/Hallucinate.io",
        "https://github.com/mdrmtz/Hallucinate.io.git",
        "git@github.com:mdrmtz/Hallucinate.io.git",
        "ssh://git@github.com/mdrmtz/Hallucinate.io.git",
    ],
)
def test_is_url_detects_git_urls(source: str) -> None:
    assert is_url(source) is True


def test_is_url_rejects_plain_local_path() -> None:
    assert is_url("/Users/dev/some/local/checkout") is False
    assert is_url("relative/path") is False


def test_resolve_repo_source_local_path_returns_as_is(tmp_path: Path) -> None:
    local_repo = tmp_path / "existing-checkout"
    local_repo.mkdir()

    resolved = resolve_repo_source(str(local_repo), cache_dir=tmp_path / "cache")

    assert resolved == local_repo.resolve()


def test_resolve_repo_source_raises_for_missing_local_path(tmp_path: Path) -> None:
    with pytest.raises(RepoSourceError, match="does not exist"):
        resolve_repo_source(str(tmp_path / "nope"), cache_dir=tmp_path / "cache")


@pytest.fixture
def source_git_repo(tmp_path: Path) -> Path:
    """A real, disposable git repo named `*.git` so `is_url()` treats it as clonable."""
    repo = tmp_path / "upstream.git"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)  # noqa: S603, S607
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)  # noqa: S603, S607
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)  # noqa: S603, S607
    (repo / "angular.json").write_text("{}\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)  # noqa: S603, S607
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repo, check=True)  # noqa: S603, S607
    return repo


def test_resolve_repo_source_clones_git_url(source_git_repo: Path, tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"

    resolved = resolve_repo_source(str(source_git_repo), cache_dir=cache_dir)

    assert resolved == cache_dir / "upstream"
    assert (resolved / "angular.json").is_file()


def test_resolve_repo_source_reuses_existing_clone(source_git_repo: Path, tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    first = resolve_repo_source(str(source_git_repo), cache_dir=cache_dir)
    (first / "sentinel.txt").write_text("still here\n", encoding="utf-8")

    second = resolve_repo_source(str(source_git_repo), cache_dir=cache_dir)

    assert second == first
    assert (second / "sentinel.txt").is_file()


def test_resolve_repo_source_raises_on_clone_failure(tmp_path: Path) -> None:
    not_a_repo = "/definitely/not/a/real/repo.git"
    with pytest.raises(RepoSourceError, match="git clone failed"):
        resolve_repo_source(not_a_repo, cache_dir=tmp_path / "cache")


def _fake_npm_run(monkeypatch: pytest.MonkeyPatch, *, returncode: int = 0, stderr: str = "") -> list[list[str]]:
    """Patches `repo_source.subprocess.run` so `npm ...` calls are faked
    (recorded, and simulated as creating `node_modules` on success) while
    every other command (git) goes through to the real `subprocess.run` -
    real `npm install` needs network access this test suite shouldn't
    depend on.
    """
    calls: list[list[str]] = []
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):  # noqa: ANN001, ANN003, ANN202
        if cmd[0] == "npm":
            calls.append(list(cmd))
            if returncode == 0:
                Path(kwargs["cwd"]).joinpath("node_modules").mkdir(exist_ok=True)
            return subprocess.CompletedProcess(cmd, returncode, stdout="", stderr=stderr)
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(repo_source.subprocess, "run", fake_run)
    return calls


def test_resolve_repo_source_runs_npm_install_after_fresh_clone(
    source_git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test: `resolve_repo_source()` used to only `git clone` and
    never install dependencies, so every downstream `npx ng build`/`ng
    serve` call failed on a fresh repo with a missing-`node_modules` error.
    """
    (source_git_repo / "package.json").write_text('{"name": "demo"}\n', encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=source_git_repo, check=True)  # noqa: S603, S607
    subprocess.run(["git", "commit", "-q", "-m", "add package.json"], cwd=source_git_repo, check=True)  # noqa: S603, S607
    calls = _fake_npm_run(monkeypatch)

    resolved = resolve_repo_source(str(source_git_repo), cache_dir=tmp_path / "cache")

    assert calls == [["npm", "install"]]
    assert (resolved / "node_modules").is_dir()


def test_resolve_repo_source_runs_npm_install_for_a_local_path_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gap wasn't clone-specific: a caller-supplied local checkout with
    no `node_modules` needs the same install before it's build-ready.
    """
    local_repo = tmp_path / "existing-checkout"
    local_repo.mkdir()
    (local_repo / "package.json").write_text('{"name": "demo"}\n', encoding="utf-8")
    calls = _fake_npm_run(monkeypatch)

    resolved = resolve_repo_source(str(local_repo), cache_dir=tmp_path / "cache")

    assert calls == [["npm", "install"]]
    assert (resolved / "node_modules").is_dir()


def test_resolve_repo_source_skips_npm_install_when_node_modules_already_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local_repo = tmp_path / "existing-checkout"
    local_repo.mkdir()
    (local_repo / "package.json").write_text('{"name": "demo"}\n', encoding="utf-8")
    (local_repo / "node_modules").mkdir()
    calls = _fake_npm_run(monkeypatch)

    resolve_repo_source(str(local_repo), cache_dir=tmp_path / "cache")

    assert calls == []


def test_resolve_repo_source_skips_npm_install_when_no_package_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local_repo = tmp_path / "existing-checkout"
    local_repo.mkdir()
    calls = _fake_npm_run(monkeypatch)

    resolve_repo_source(str(local_repo), cache_dir=tmp_path / "cache")

    assert calls == []


def test_resolve_repo_source_reuse_path_installs_if_node_modules_missing(
    source_git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cached clone from before this fix (or one where `node_modules` was
    later wiped) must still get installed on a later run, not just on the
    very first clone.
    """
    cache_dir = tmp_path / "cache"
    first = resolve_repo_source(str(source_git_repo), cache_dir=cache_dir)
    (first / "package.json").write_text('{"name": "demo"}\n', encoding="utf-8")
    calls = _fake_npm_run(monkeypatch)

    second = resolve_repo_source(str(source_git_repo), cache_dir=cache_dir)

    assert second == first
    assert calls == [["npm", "install"]]
    assert (second / "node_modules").is_dir()


def test_resolve_repo_source_raises_repo_source_error_on_npm_install_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local_repo = tmp_path / "existing-checkout"
    local_repo.mkdir()
    (local_repo / "package.json").write_text('{"name": "demo"}\n', encoding="utf-8")
    _fake_npm_run(monkeypatch, returncode=1, stderr="npm ERR! network timeout")

    with pytest.raises(RepoSourceError, match="npm install failed"):
        resolve_repo_source(str(local_repo), cache_dir=tmp_path / "cache")


def test_resolve_repo_source_raises_repo_source_error_on_npm_install_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local_repo = tmp_path / "existing-checkout"
    local_repo.mkdir()
    (local_repo / "package.json").write_text('{"name": "demo"}\n', encoding="utf-8")

    def fake_run(cmd, **kwargs):  # noqa: ANN001, ANN003, ANN202
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 0))

    monkeypatch.setattr(repo_source.subprocess, "run", fake_run)

    with pytest.raises(RepoSourceError, match="npm install timed out"):
        resolve_repo_source(str(local_repo), cache_dir=tmp_path / "cache")


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("https://github.com/mdrmtz/Hallucinate.io", "mdrmtz/Hallucinate.io"),
        ("https://github.com/mdrmtz/Hallucinate.io.git", "mdrmtz/Hallucinate.io"),
        ("https://github.com/mdrmtz/Hallucinate.io/", "mdrmtz/Hallucinate.io"),
        ("git@github.com:mdrmtz/Hallucinate.io.git", "mdrmtz/Hallucinate.io"),
        ("ssh://git@github.com/mdrmtz/Hallucinate.io.git", "mdrmtz/Hallucinate.io"),
        ("https://github.com/ACME/their-app", "ACME/their-app"),
    ],
)
def test_derive_github_repo_parses_github_url(source: str, expected: str) -> None:
    assert derive_github_repo(source) == expected


def test_derive_github_repo_returns_none_for_non_github_url() -> None:
    assert derive_github_repo("https://gitlab.com/mdrmtz/Hallucinate.io.git") is None


def test_derive_github_repo_returns_none_for_local_path_without_origin(tmp_path: Path) -> None:
    local_repo = tmp_path / "no-remote-checkout"
    local_repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=local_repo, check=True)  # noqa: S603, S607

    assert derive_github_repo(str(local_repo), resolved_path=local_repo) is None


def test_derive_github_repo_returns_none_when_no_resolved_path_given() -> None:
    assert derive_github_repo("/some/local/checkout") is None


def test_derive_github_repo_falls_back_to_origin_remote_for_local_path(tmp_path: Path) -> None:
    local_repo = tmp_path / "checkout-with-remote"
    local_repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=local_repo, check=True)  # noqa: S603, S607
    subprocess.run(  # noqa: S603, S607
        ["git", "remote", "add", "origin", "https://github.com/ACME/their-app.git"],
        cwd=local_repo,
        check=True,
    )

    assert derive_github_repo(str(local_repo), resolved_path=local_repo) == "ACME/their-app"
