from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from a11y_fixer.adapters.sandbox.docker_backend import DockerSandboxBackend, DockerSandboxError


@pytest.fixture
def backend(tmp_path: Path) -> DockerSandboxBackend:
    return DockerSandboxBackend(workdir=tmp_path, container_name="test-container")


def test_id_returns_container_name(backend: DockerSandboxBackend) -> None:
    assert backend.id == "test-container"


def test_start_runs_docker_run_with_bind_mount(backend: DockerSandboxBackend, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict = {}

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        captured["cmd"] = cmd
        return MagicMock(returncode=0, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    backend.start()

    cmd = captured["cmd"]
    assert cmd[:3] == ["docker", "run", "-d"]
    assert "--name" in cmd and "test-container" in cmd
    assert f"{tmp_path.resolve()}:/workspace" in cmd


def test_start_raises_on_docker_failure(backend: DockerSandboxBackend, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: MagicMock(returncode=1, stderr="no such image"))  # noqa: ARG005

    with pytest.raises(DockerSandboxError, match="docker run failed"):
        backend.start()


def test_start_is_idempotent(backend: DockerSandboxBackend, monkeypatch: pytest.MonkeyPatch) -> None:
    run_mock = MagicMock(return_value=MagicMock(returncode=0, stderr=""))
    monkeypatch.setattr(subprocess, "run", run_mock)

    backend.start()
    backend.start()

    assert run_mock.call_count == 1


def test_stop_calls_docker_rm_force(backend: DockerSandboxBackend, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        captured["cmd"] = cmd
        return MagicMock(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    backend.stop()

    assert captured["cmd"] == ["docker", "rm", "-f", "test-container"]


def test_context_manager_starts_and_stops(backend: DockerSandboxBackend, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(backend, "start", lambda: calls.append("start"))
    monkeypatch.setattr(backend, "stop", lambda: calls.append("stop"))

    with backend:
        calls.append("inside")

    assert calls == ["start", "inside", "stop"]


def test_execute_runs_docker_exec_and_wraps_output(backend: DockerSandboxBackend, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **k: MagicMock(returncode=0, stdout="hello\n", stderr="")  # noqa: ARG005
        if cmd[:2] == ["docker", "exec"]
        else MagicMock(returncode=0),
    )

    result = backend.execute("echo hello")

    assert result.output == "hello\n"
    assert result.exit_code == 0
    assert result.truncated is False


def test_execute_on_timeout_returns_truncated_response(backend: DockerSandboxBackend, monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_timeout(cmd: list[str], **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=1, output="partial", stderr="")

    monkeypatch.setattr(subprocess, "run", raise_timeout)

    result = backend.execute("sleep 999", timeout=1)

    assert result.exit_code is None
    assert result.truncated is True
    assert "partial" in result.output


def test_upload_files_copies_content_via_docker_cp(backend: DockerSandboxBackend, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        calls.append(cmd)
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    responses = backend.upload_files([("/workspace/app.component.html", b"<div></div>")])

    assert responses[0].error is None
    assert responses[0].path == "/workspace/app.component.html"
    cp_calls = [c for c in calls if c[:2] == ["docker", "cp"]]
    assert len(cp_calls) == 1
    assert cp_calls[0][3] == "test-container:/workspace/app.component.html"


def test_upload_files_reports_error_when_mkdir_fails(backend: DockerSandboxBackend, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: MagicMock(returncode=1, stdout="permission denied", stderr=""))  # noqa: ARG005

    responses = backend.upload_files([("/workspace/blocked.html", b"x")])

    assert responses[0].error is not None


def test_download_files_reads_content_via_docker_cp(backend: DockerSandboxBackend, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        if cmd[:2] == ["docker", "cp"]:
            # simulate docker cp by writing the requested content to the destination
            dest = Path(cmd[3])
            dest.write_bytes(b"downloaded content")
            return MagicMock(returncode=0, stdout="", stderr="")
        return MagicMock(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    responses = backend.download_files(["/workspace/report.json"])

    assert responses[0].error is None
    assert responses[0].content == b"downloaded content"


def test_download_files_reports_file_not_found(backend: DockerSandboxBackend, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: MagicMock(returncode=1, stdout="", stderr="no such file"))  # noqa: ARG005

    responses = backend.download_files(["/workspace/missing.json"])

    assert responses[0].error == "file_not_found"
