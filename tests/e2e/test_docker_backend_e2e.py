"""Real end-to-end test against an actual Docker daemon. Uses `python:3.12-slim`
(small, fast to pull, and - unlike `alpine` - ships `python3`, which
`BaseSandbox`'s derived read/glob/grep/edit operations shell out to) rather
than the full `a11y-fixer-sandbox` image built from `sandbox/Dockerfile`,
since this test only needs to verify the backend's execute/upload/download/
lifecycle mechanics, not the Angular/Playwright toolchain. Skipped by
default; run explicitly with `pytest tests/e2e/ -m e2e`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from a11y_fixer.adapters.sandbox.docker_backend import DockerSandboxBackend

pytestmark = pytest.mark.e2e


@pytest.fixture
def sandbox_backend(tmp_path: Path) -> DockerSandboxBackend:
    return DockerSandboxBackend(workdir=tmp_path, image="python:3.12-slim", container_name="a11y-fixer-e2e-test")


def _container_is_running(name: str) -> bool:
    result = subprocess.run(  # noqa: S603, S607
        ["docker", "ps", "--filter", f"name=^{name}$", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return name in result.stdout.split()


def test_real_container_lifecycle_execute_and_file_transfer(sandbox_backend: DockerSandboxBackend) -> None:
    try:
        sandbox_backend.start()
        assert _container_is_running(sandbox_backend.id)

        result = sandbox_backend.execute("echo hello-from-container")
        assert result.exit_code == 0
        assert "hello-from-container" in result.output

        upload = sandbox_backend.upload_files([("/workspace/nested/test.html", b"<div>fixed</div>")])
        assert upload[0].error is None

        download = sandbox_backend.download_files(["/workspace/nested/test.html"])
        assert download[0].error is None
        assert download[0].content == b"<div>fixed</div>"

        # BaseSandbox-derived filesystem ops work through execute() too.
        read_result = sandbox_backend.read("/workspace/nested/test.html")
        assert read_result.file_data is not None
        assert "fixed" in read_result.file_data["content"]
    finally:
        sandbox_backend.stop()

    assert not _container_is_running(sandbox_backend.id)
