from __future__ import annotations

from pathlib import Path

import pytest

from a11y_fixer.fleet_config import FleetManifestError, SiteEntry, load_manifest


def _write(tmp_path: Path, text: str) -> Path:
    manifest = tmp_path / "sites.yaml"
    manifest.write_text(text, encoding="utf-8")
    return manifest


def test_load_manifest_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FleetManifestError, match="not found"):
        load_manifest(tmp_path / "does-not-exist.yaml")


def test_load_manifest_parses_a_full_single_site_entry(tmp_path: Path) -> None:
    manifest = _write(
        tmp_path,
        """
        sites:
          - repo: https://github.com/acme/marketing-site.git
            url: https://marketing.acme.com
            site_id: acme-marketing
            github_token_env: ACME_GITHUB_TOKEN
        """,
    )

    sites = load_manifest(manifest)

    assert sites == [
        SiteEntry(
            repo="https://github.com/acme/marketing-site.git",
            url="https://marketing.acme.com",
            audit=None,
            site_id="acme-marketing",
            github_token_env="ACME_GITHUB_TOKEN",
        )
    ]


def test_load_manifest_defaults_url_site_id_and_token_env(tmp_path: Path) -> None:
    """`url`, `site_id`, and `github_token_env` are all optional: `url` stays
    `None`, `site_id` is derived from the GitHub-shaped `repo`, and
    `github_token_env` falls back to `GITHUB_TOKEN`.
    """
    manifest = _write(
        tmp_path,
        """
        sites:
          - repo: https://github.com/acme/marketing-site.git
        """,
    )

    sites = load_manifest(manifest)

    assert len(sites) == 1
    site = sites[0]
    assert site.url is None
    assert site.site_id == "acme/marketing-site"
    assert site.github_token_env == "GITHUB_TOKEN"


def test_load_manifest_derives_site_id_from_local_path_when_not_github(
    tmp_path: Path,
) -> None:
    local_repo = tmp_path / "checkouts" / "my-app"
    local_repo.mkdir(parents=True)
    manifest = _write(tmp_path, f"sites:\n  - repo: {local_repo}\n")

    sites = load_manifest(manifest)

    assert sites[0].site_id == "my-app"


def test_load_manifest_missing_sites_key_raises(tmp_path: Path) -> None:
    manifest = _write(tmp_path, "not_sites: []\n")
    with pytest.raises(FleetManifestError, match="top-level 'sites:' list"):
        load_manifest(manifest)


def test_load_manifest_empty_sites_list_raises(tmp_path: Path) -> None:
    manifest = _write(tmp_path, "sites: []\n")
    with pytest.raises(FleetManifestError, match="non-empty list"):
        load_manifest(manifest)


def test_load_manifest_site_missing_repo_raises(tmp_path: Path) -> None:
    manifest = _write(tmp_path, "sites:\n  - url: https://example.com\n")
    with pytest.raises(FleetManifestError, match="missing required 'repo'"):
        load_manifest(manifest)


def test_load_manifest_rejects_raw_github_token_key(tmp_path: Path) -> None:
    manifest = _write(
        tmp_path,
        """
        sites:
          - repo: https://github.com/acme/marketing-site.git
            github_token: ghp_thisIsALeakedSecretDoNotAllow
        """,
    )

    with pytest.raises(FleetManifestError, match="github_token_env"):
        load_manifest(manifest)


def test_load_manifest_site_entry_must_be_a_mapping(tmp_path: Path) -> None:
    manifest = _write(tmp_path, "sites:\n  - just-a-string\n")
    with pytest.raises(FleetManifestError, match="must be a mapping"):
        load_manifest(manifest)


def test_load_manifest_two_sites_both_parse(tmp_path: Path) -> None:
    """`load_manifest()` itself does not enforce the "one site per
    invocation" limit - that's `cli._cmd_fleet`'s job - so the loader stays
    reusable once fleet grows to handle more than one site at a time.
    """
    manifest = _write(
        tmp_path,
        """
        sites:
          - repo: https://github.com/acme/one.git
          - repo: https://github.com/acme/two.git
        """,
    )

    sites = load_manifest(manifest)

    assert [s.repo for s in sites] == [
        "https://github.com/acme/one.git",
        "https://github.com/acme/two.git",
    ]


def test_load_manifest_audit_defaults_to_none(tmp_path: Path) -> None:
    manifest = _write(
        tmp_path,
        "sites:\n  - repo: https://github.com/acme/one.git\n",
    )

    sites = load_manifest(manifest)

    assert sites[0].audit is None


def test_load_manifest_parses_audit_field(tmp_path: Path) -> None:
    """`audit:` points fleet at a saved audit report (see `run --audit`) to
    replay instead of crawling `repo`/`url` live - the fast-testing path for
    a known violation set.
    """
    manifest = _write(
        tmp_path,
        "sites:\n"
        "  - repo: https://github.com/acme/one.git\n"
        "    audit: evaluation/results/audit.json\n",
    )

    sites = load_manifest(manifest)

    assert sites[0].audit == "evaluation/results/audit.json"


def test_load_manifest_rejects_non_string_audit(tmp_path: Path) -> None:
    manifest = _write(
        tmp_path,
        "sites:\n"
        "  - repo: https://github.com/acme/one.git\n"
        "    audit: [1, 2, 3]\n",
    )

    with pytest.raises(FleetManifestError, match="audit must be a string"):
        load_manifest(manifest)
