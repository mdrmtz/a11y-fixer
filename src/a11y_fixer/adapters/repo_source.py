"""Resolves the target repo to audit/fix: a local path is used as-is, a git
URL is shallow-cloned into a cache directory. This is what makes the
fixture pluggable via a single `--repo` CLI argument instead of hardcoding
`Hallucinate.io` - point `a11y-fixer` at any Angular repo's URL or checkout.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_URL_RE = re.compile(r"^(https?://|git@|ssh://)")

_GITHUB_URL_RE = re.compile(
    r"^(?:https?://github\.com/|git@github\.com:|ssh://git@github\.com/)"
    r"(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"
)

# npm install can legitimately take a while on a large/cold dependency tree;
# generous but bounded so a hung install doesn't hang the whole pipeline.
NPM_INSTALL_TIMEOUT_SECONDS = 600


class RepoSourceError(RuntimeError):
    """Raised when resolving or cloning the target repo fails."""


def is_url(source: str) -> bool:
    """True if `source` looks like a git URL rather than a local path."""
    stripped = source.strip()
    return bool(_URL_RE.match(stripped)) or stripped.endswith(".git")


def _slug_for(url: str) -> str:
    name = url.rstrip("/").rsplit("/", 1)[-1]
    return re.sub(r"\.git$", "", name) or "repo"


def ensure_dependencies_installed(path: Path) -> None:
    """Run `npm install` in `path` if it's an npm project that isn't
    installed yet.

    A `git clone` (or a local checkout the caller points us at) only ever
    fetches source - it never installs dependencies. Without this, every
    downstream `npx ng build`/`ng serve` call (`html_lang_applier.py`,
    `audit_runner.py`) fails on a fresh repo with a missing-`node_modules`
    error that looks unrelated to the real, mundane cause. Runs for BOTH
    the freshly-cloned path and a caller-supplied local path (and is a
    no-op on a cache hit whose `node_modules` is already there), so this is
    the one place that guarantees "downstream tooling can assume the repo
    is ready to build" regardless of how `resolve_repo_source()` got here.

    Public (no leading underscore) because `cli.py` also calls this
    directly for the bundled fixture path when no `--repo` override
    resolved a path itself - see `_apply_repo_override()`.
    """
    if not (path / "package.json").is_file():
        return  # not an npm project - nothing to install

    if (path / "node_modules").is_dir():
        return  # already installed (repeat run against a cached clone)

    try:
        result = subprocess.run(  # noqa: S603, S607 - fixed subcommand, path is our own resolved clone/checkout
            ["npm", "install"],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=NPM_INSTALL_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        msg = f"npm install timed out after {NPM_INSTALL_TIMEOUT_SECONDS}s in {path}"
        raise RepoSourceError(msg) from exc

    if result.returncode != 0:
        msg = f"npm install failed in {path} (exit {result.returncode}):\n{result.stderr}"
        raise RepoSourceError(msg)


def resolve_repo_source(source: str, *, cache_dir: Path) -> Path:
    """Return a local, checked-out, dependency-installed path for `source`.

    A local directory path is resolved and used as-is. A git URL is
    shallow-cloned (`--depth 1`) into `cache_dir`, reusing an existing clone
    at the same URL-derived directory name if one is already present rather
    than re-cloning on every run. Either way, before returning, `npm
    install` is run in the resolved path if it looks like an npm project
    that hasn't been installed yet (see `ensure_dependencies_installed`).
    """
    stripped = source.strip()
    if not is_url(stripped):
        path = Path(stripped).expanduser().resolve()
        if not path.is_dir():
            msg = f"local repo path does not exist: {path}"
            raise RepoSourceError(msg)
        ensure_dependencies_installed(path)
        return path

    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / _slug_for(stripped)
    if target.is_dir():
        ensure_dependencies_installed(target)
        return target

    result = subprocess.run(  # noqa: S603, S607 - fixed subcommand, source is the caller's own --repo argument
        ["git", "clone", "--depth", "1", stripped, str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        msg = f"git clone failed for {stripped!r} (exit {result.returncode}):\n{result.stderr}"
        raise RepoSourceError(msg)
    ensure_dependencies_installed(target)
    return target


def derive_github_repo(source: str, *, resolved_path: Path | None = None) -> str | None:
    """Best-effort `owner/repo` for GitHub PR delivery, derived from `source`.

    `source` is whatever was passed to `--repo` (a URL or a local path) - this
    is what closes the gap where `config.resolve_pr_delivery()` used to read a
    static `GITHUB_REPO` env var with no relation to `--repo` at all, so PR
    delivery for a dynamically-targeted repo (ACME, ABC, ...) would silently
    target whatever `GITHUB_REPO` happened to be exported as instead.

    A GitHub URL is parsed directly. A local path (or any source that isn't a
    recognizable GitHub URL) falls back to reading `resolved_path`'s own
    `origin` remote, if it has one and it points at GitHub. Returns `None`
    when nothing github.com-shaped can be found, so callers can fall back to
    their own default rather than silently mis-targeting PR delivery.
    """
    match = _GITHUB_URL_RE.match(source.strip())
    if match:
        return f"{match['owner']}/{match['repo']}"

    if resolved_path is None or not resolved_path.is_dir():
        return None

    result = subprocess.run(  # noqa: S603, S607 - fixed subcommand, resolved_path is our own resolved clone/checkout
        ["git", "-C", str(resolved_path), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None

    remote_match = _GITHUB_URL_RE.match(result.stdout.strip())
    return f"{remote_match['owner']}/{remote_match['repo']}" if remote_match else None
