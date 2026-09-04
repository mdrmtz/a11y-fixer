"""Docker sandbox backend: a drop-in replacement for the local execution
backend, with zero changes to domain or subagent code (`BaseSandbox` derives
every filesystem operation - `ls`/`read`/`write`/`edit`/`glob`/`grep` - from
`execute()`, so only `execute()`, `upload_files()`, `download_files()`, and
`id` need implementing here). One ephemeral `docker run --rm` container per
ToT node evaluation; `stop()` runs `docker rm -f` unconditionally so nothing
outlives its evaluation, on both success and failure paths.
"""

from __future__ import annotations

import shlex
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Self

from deepagents.backends.protocol import FILE_NOT_FOUND, ExecuteResponse, FileDownloadResponse, FileUploadResponse
from deepagents.backends.sandbox import BaseSandbox

# Built from agent/sandbox/Dockerfile: the official Playwright image
# (Chromium, OS deps, Node LTS) plus a global Angular CLI install.
DEFAULT_IMAGE = "a11y-fixer-sandbox:latest"
DEFAULT_EXECUTE_TIMEOUT = 120
DEFAULT_START_TIMEOUT = 60


class DockerSandboxError(RuntimeError):
    """Raised when a Docker sandbox operation fails."""


class DockerSandboxBackend(BaseSandbox):
    """Executes shell commands and file transfers inside one Docker container."""

    enable_capture_offload = False

    def __init__(
        self,
        *,
        workdir: Path,
        image: str = DEFAULT_IMAGE,
        container_name: str | None = None,
        node_modules_volume: str | None = None,
        extra_run_args: list[str] | None = None,
        default_timeout: int = DEFAULT_EXECUTE_TIMEOUT,
        mount_target: str = "/workspace",
    ) -> None:
        self._workdir = workdir.resolve()
        self._image = image
        self._container_name = container_name or f"a11y-fixer-{uuid.uuid4().hex[:12]}"
        self._node_modules_volume = node_modules_volume
        self._extra_run_args = extra_run_args or []
        self._default_timeout = default_timeout
        self._mount_target = mount_target
        self._started = False

    @property
    def id(self) -> str:
        return self._container_name

    def start(self) -> None:
        """Start the ephemeral container, bind-mounting `workdir` at `mount_target`.

        `node_modules` is mounted as a named Docker volume rather than bind
        or symlinked from the host: platform-specific prebuilt binaries
        (e.g. esbuild/sass) in a macOS `node_modules` are not usable inside
        a Linux container. Pass `node_modules_volume` and run `npm ci`
        inside the container to populate it.
        """
        if self._started:
            return
        cmd = [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            self._container_name,
            "-v",
            f"{self._workdir}:{self._mount_target}",
        ]
        if self._node_modules_volume:
            cmd += ["-v", f"{self._node_modules_volume}:{self._mount_target}/node_modules"]
        cmd += self._extra_run_args
        cmd += [self._image, "sleep", "infinity"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=DEFAULT_START_TIMEOUT, check=False)  # noqa: S603
        if result.returncode != 0:
            msg = f"docker run failed (exit {result.returncode}):\n{result.stderr}"
            raise DockerSandboxError(msg)
        self._started = True

    def stop(self) -> None:
        """Force-remove the container. Safe to call even if it never started."""
        subprocess.run(  # noqa: S603, S607
            ["docker", "rm", "-f", self._container_name],
            capture_output=True,
            text=True,
            check=False,
        )
        self._started = False

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        effective_timeout = timeout if timeout is not None else self._default_timeout
        cmd = ["docker", "exec", self._container_name, "sh", "-lc", command]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=effective_timeout, check=False)  # noqa: S603
        except subprocess.TimeoutExpired as exc:
            output = (exc.stdout or "") + (exc.stderr or "")
            return ExecuteResponse(output=output, exit_code=None, truncated=True)
        return ExecuteResponse(output=result.stdout + result.stderr, exit_code=result.returncode)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        responses: list[FileUploadResponse] = []
        for path, content in files:
            tmp_path = Path(tempfile.mkstemp()[1])
            try:
                tmp_path.write_bytes(content)
                mkdir_result = self.execute(f"mkdir -p {shlex.quote(str(Path(path).parent))}")
                if mkdir_result.exit_code != 0:
                    responses.append(FileUploadResponse(path=path, error=mkdir_result.output[:200]))
                    continue
                copy_result = subprocess.run(  # noqa: S603, S607
                    ["docker", "cp", str(tmp_path), f"{self._container_name}:{path}"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if copy_result.returncode != 0:
                    responses.append(FileUploadResponse(path=path, error=copy_result.stderr[:200]))
                else:
                    responses.append(FileUploadResponse(path=path))
            finally:
                tmp_path.unlink(missing_ok=True)
        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses: list[FileDownloadResponse] = []
        for path in paths:
            tmp_path = Path(tempfile.mkstemp()[1])
            try:
                result = subprocess.run(  # noqa: S603, S607
                    ["docker", "cp", f"{self._container_name}:{path}", str(tmp_path)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode != 0:
                    responses.append(FileDownloadResponse(path=path, error=FILE_NOT_FOUND))
                else:
                    responses.append(FileDownloadResponse(path=path, content=tmp_path.read_bytes()))
            finally:
                tmp_path.unlink(missing_ok=True)
        return responses
