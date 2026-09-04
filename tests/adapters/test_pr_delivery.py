from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from a11y_fixer.adapters.pr.delivery import (
    FileChange,
    PRDeliveryError,
    PullRequestPlan,
    deliver,
    deliver_dry_run,
    deliver_live,
    render_unified_diff,
)
from a11y_fixer.config import PRDeliveryConfig


def _plan() -> PullRequestPlan:
    return PullRequestPlan(
        title="Fix: add lang attribute to <html>",
        body="Resolves html-has-lang (WCAG 3.1.1) site-wide.",
        branch_name="a11y-fixer/html-has-lang",
        changes=[FileChange(path="src/index.html", old_content="<html>\n", new_content='<html lang="en">\n')],
    )


def test_render_unified_diff_shows_the_change() -> None:
    diff = render_unified_diff(_plan().changes)
    assert "-<html>" in diff
    assert '+<html lang="en">' in diff


def test_deliver_dry_run_writes_diff_and_description(tmp_path: Path) -> None:
    result = deliver_dry_run(_plan(), output_dir=tmp_path)

    assert result.diff_path.exists()
    assert result.description_path.exists()
    assert '+<html lang="en">' in result.diff_path.read_text(encoding="utf-8")
    assert "Fix: add lang attribute" in result.description_path.read_text(encoding="utf-8")


def test_deliver_dispatches_to_dry_run_when_not_live(tmp_path: Path) -> None:
    config = PRDeliveryConfig(live=False, github_token=None, github_repo=None)
    result = deliver(_plan(), config=config, output_dir=tmp_path)
    assert result.diff_path.exists()  # type: ignore[union-attr]


def test_deliver_raises_when_live_missing_credentials(tmp_path: Path) -> None:
    config = PRDeliveryConfig(live=True, github_token=None, github_repo=None)
    with pytest.raises(PRDeliveryError, match="requires both"):
        deliver(_plan(), config=config, output_dir=tmp_path)


def test_deliver_live_invalid_repo_format_raises() -> None:
    with pytest.raises(PRDeliveryError, match="owner/repo"):
        deliver_live(_plan(), github_token="tok", github_repo="not-a-valid-repo-format")


def _handler_success(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if request.method == "GET" and path.endswith("/git/ref/heads/main"):
        return httpx.Response(200, json={"object": {"sha": "base-sha-123"}})
    if request.method == "POST" and path.endswith("/git/refs"):
        return httpx.Response(201, json={"ref": "refs/heads/a11y-fixer/html-has-lang"})
    if request.method == "GET" and "/contents/" in path:
        return httpx.Response(404, json={"message": "Not Found"})
    if request.method == "PUT" and "/contents/" in path:
        return httpx.Response(201, json={"content": {"path": "src/index.html"}})
    if request.method == "POST" and path.endswith("/pulls"):
        return httpx.Response(201, json={"html_url": "https://github.com/mdrmtz/Hallucinate.io/pull/42", "number": 42})
    return httpx.Response(500, json={"message": f"unexpected request {request.method} {path}"})


def test_deliver_live_creates_branch_commits_and_opens_pr() -> None:
    client = httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(_handler_success))

    result = deliver_live(_plan(), github_token="tok", github_repo="mdrmtz/Hallucinate.io", client=client)

    assert result.pull_request_number == 42
    assert result.pull_request_url == "https://github.com/mdrmtz/Hallucinate.io/pull/42"
    assert result.branch_name == "a11y-fixer/html-has-lang"


def test_deliver_live_reuses_existing_file_sha_on_update() -> None:
    put_payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": "base-sha-123"}})
        if request.method == "POST" and path.endswith("/git/refs"):
            return httpx.Response(201, json={})
        if request.method == "GET" and "/contents/" in path:
            return httpx.Response(200, json={"sha": "existing-file-sha"})
        if request.method == "PUT" and "/contents/" in path:
            put_payloads.append(json.loads(request.content))
            return httpx.Response(200, json={"content": {"path": "src/index.html"}})
        if request.method == "POST" and path.endswith("/pulls"):
            return httpx.Response(201, json={"html_url": "https://github.com/x/y/pull/1", "number": 1})
        return httpx.Response(500)

    client = httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler))
    deliver_live(_plan(), github_token="tok", github_repo="mdrmtz/Hallucinate.io", client=client)

    assert put_payloads[0]["sha"] == "existing-file-sha"


def test_deliver_live_raises_on_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(422, json={"message": "Reference already exists"})

    client = httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler))

    with pytest.raises(PRDeliveryError, match="422"):
        deliver_live(_plan(), github_token="tok", github_repo="mdrmtz/Hallucinate.io", client=client)
