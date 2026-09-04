"""Loads a fleet manifest: a YAML list of sites to run `a11y-fixer run`
against, one invocation per site. See `sites.example.yaml` for the schema.

`fleet` (in `cli.py`) currently only accepts a manifest with exactly one
site - the HITL queue, `.violation_status.json`, and audit-output paths
(`config.hitl_queue_dir()` et al.) are all global/unnamespaced, so running
more than one site through the same process risks one site's state
corrupting another's. That limit is enforced by the caller, not here, so
this module stays reusable once fleet grows beyond one site.

Security note: a manifest is meant to be checked into git, so it must never
carry a real secret. `github_token_env` is the NAME of an environment
variable holding the token (e.g. exported in the shell, or set in a
gitignored `.env`) - `load_manifest()` rejects outright any site entry that
instead has a literal `github_token` key, since that would almost certainly
be a real token pasted in by mistake.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

import yaml

from a11y_fixer.adapters.repo_source import derive_github_repo

DEFAULT_GITHUB_TOKEN_ENV = "GITHUB_TOKEN"


class FleetManifestError(RuntimeError):
    """Raised when a fleet manifest is missing, malformed, or unsafe."""


@dataclass(frozen=True)
class SiteEntry:
    """One `sites:` entry, fully resolved (defaults applied)."""

    repo: str
    url: str | None
    audit: str | None
    site_id: str
    github_token_env: str


def _derive_site_id(repo: str) -> str:
    """Best-effort human-readable id when a site omits `site_id`."""
    github_repo = derive_github_repo(repo)
    if github_repo:
        return github_repo
    return Path(repo.rstrip("/")).name or repo


def _fail(manifest_path: Path, message: str) -> NoReturn:
    msg = f"fleet manifest {manifest_path}: {message}"
    raise FleetManifestError(msg)


def _optional_str_field(
    manifest_path: Path, site: dict, index: int, field: str
) -> str | None:
    value = site.get(field)
    if value is not None and not isinstance(value, str):
        _fail(manifest_path, f"sites[{index}].{field} must be a string")
    return value


def load_manifest(path: str | Path) -> list[SiteEntry]:
    """Parse a `sites:` YAML manifest into `SiteEntry` objects.

    Raises `FleetManifestError` for: a missing file, a manifest with no
    top-level `sites:` list, a site entry missing `repo`, or a site entry
    carrying a literal `github_token` key (see module docstring).
    """
    manifest_path = Path(path)
    if not manifest_path.is_file():
        msg = f"fleet manifest not found: {manifest_path}"
        raise FleetManifestError(msg)

    raw: Any = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict) or "sites" not in raw:
        _fail(manifest_path, "must have a top-level 'sites:' list")

    sites = raw["sites"]
    if not isinstance(sites, list) or not sites:
        _fail(manifest_path, "'sites' must be a non-empty list")

    entries: list[SiteEntry] = []
    for i, site in enumerate(sites):
        if not isinstance(site, dict):
            _fail(manifest_path, f"sites[{i}] must be a mapping")

        if "github_token" in site:
            _fail(
                manifest_path,
                f"sites[{i}] has a literal 'github_token' key - never put a "
                "real token in a manifest (it's meant to be checked into "
                "git). Use 'github_token_env' with the NAME of an "
                "environment variable that holds the token instead "
                f"(default: {DEFAULT_GITHUB_TOKEN_ENV}).",
            )

        repo = site.get("repo")
        if not repo or not isinstance(repo, str):
            _fail(manifest_path, f"sites[{i}] is missing required 'repo'")

        url = _optional_str_field(manifest_path, site, i, "url")
        # Path to a saved audit report (see `run --audit`): when set, fleet
        # replays this report instead of running a fresh audit against
        # `repo`/`url` - the fast-testing path for a known violation set,
        # or for a live site whose route discovery is unreliable.
        audit = _optional_str_field(manifest_path, site, i, "audit")

        github_token_env = site.get("github_token_env") or DEFAULT_GITHUB_TOKEN_ENV
        if not isinstance(github_token_env, str):
            _fail(manifest_path, f"sites[{i}].github_token_env must be a string")

        site_id = site.get("site_id") or _derive_site_id(repo)
        if not isinstance(site_id, str):
            _fail(manifest_path, f"sites[{i}].site_id must be a string")

        entries.append(
            SiteEntry(
                repo=repo,
                url=url,
                audit=audit,
                site_id=site_id,
                github_token_env=github_token_env,
            )
        )

    return entries
