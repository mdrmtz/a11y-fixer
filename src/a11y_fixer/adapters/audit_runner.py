"""Lifecycle-managed axe-core audit runner.

`AxeAuditRunner.run()` starts `ng serve` for the fixture, runs
`npx @axe-core/cli` against every page in one pass, normalizes the combined
report, and tears the dev server down - regardless of success or failure.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic, sleep

from a11y_fixer.domain.guardrail_rules import validate_raw_axe_reports

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4200
DEFAULT_STARTUP_TIMEOUT_SECONDS = 90.0
DEFAULT_AUDIT_TIMEOUT_SECONDS = 300.0
DEFAULT_TAGS: tuple[str, ...] = ("wcag2a", "wcag2aa")


class AuditRunnerError(RuntimeError):
    """Raised when the dev server fails to start or axe-core fails to run."""


def _is_port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


@dataclass
class AxeAuditRunner:
    """Manages one `ng serve` process and drives `@axe-core/cli` against it."""

    fixture_path: Path
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    startup_timeout_seconds: float = DEFAULT_STARTUP_TIMEOUT_SECONDS
    tags: tuple[str, ...] = DEFAULT_TAGS
    _process: subprocess.Popen | None = field(default=None, init=False, repr=False)

    def start_server(self) -> None:
        """Start `ng serve`, or reuse an already-running server on the same port."""
        if not self.fixture_path.exists():
            msg = f"fixture path does not exist: {self.fixture_path}"
            raise AuditRunnerError(msg)
        if _is_port_open(self.host, self.port):
            return
        # Fixed argv, no shell, no untrusted input.
        self._process = subprocess.Popen(  # noqa: S603
            ["npx", "ng", "serve", "--host", self.host, "--port", str(self.port)],
            cwd=self.fixture_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self._wait_for_server()

    def _wait_for_server(self) -> None:
        deadline = monotonic() + self.startup_timeout_seconds
        while monotonic() < deadline:
            if _is_port_open(self.host, self.port):
                return
            if self._process is not None and self._process.poll() is not None:
                output = self._process.stdout.read() if self._process.stdout else ""
                msg = f"ng serve exited early (code {self._process.returncode}):\n{output[-2000:]}"
                raise AuditRunnerError(msg)
            sleep(0.5)
        msg = f"ng serve did not become ready within {self.startup_timeout_seconds}s"
        raise AuditRunnerError(msg)

    def stop_server(self) -> None:
        """Terminate the managed `ng serve` process, if one was started."""
        if self._process is None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=10)
        self._process = None

    def audit_pages(self, pages: tuple[str, ...]) -> dict:
        """Run axe-core against every page in one CLI invocation and normalize the result."""
        # http:// is correct here: `ng serve` is a local dev server with no TLS.
        urls = [f"http://{self.host}:{self.port}{page}" for page in pages]  # noqa: S310
        return self.audit_urls(urls)

    def audit_urls(self, urls: list[str]) -> dict:
        """Run axe-core against arbitrary full URLs in one CLI invocation and
        normalize the result - unlike `audit_pages()`, these don't have to be
        this runner's own managed `host`/`port`, and no server lifecycle is
        touched here: a live external site needs nothing started, a local one
        needs `start_server()` called first by the caller.
        """
        npx = shutil.which("npx")
        if npx is None:
            msg = "npx not found on PATH"
            raise AuditRunnerError(msg)

        # No --browser flag: @axe-core/cli's parseBrowser() only accepts an *omitted*
        # value (defaults internally to the 'chrome-headless' sentinel, which
        # selects its dedicated ServiceBuilder + headless ChromeOptions code path)
        # or a prefix of chrome/firefox/ie/safari/edge. Passing the literal string
        # "chrome-headless" is rejected with "Unknown browser chrome-headless" -
        # confirmed by reading node_modules/@axe-core/cli/dist/src/lib/utils.js.
        #
        # --chromedriver-path: use the bundled chromedriver binary from node_modules
        # to avoid auto-detection failures when system chromedriver is absent or
        # version-mismatched (this was the actual root cause of the 300 s hangs -
        # a version-mismatched chromedriver stalls session creation indefinitely).
        # Graceful fallback when node_modules is missing.
        chromedriver_bin = self.fixture_path / "node_modules" / "chromedriver" / "bin" / "chromedriver"
        cmd = [
            npx, "@axe-core/cli",
            "--timeout", "60",  # per-page axe timeout in seconds (default 90)
            *([f"--chromedriver-path={chromedriver_bin}"] if chromedriver_bin.exists() else []),
            *urls,
            "--tags", ",".join(self.tags),
            "--stdout",
        ]
        # Dynamic subprocess timeout: 90 s per URL + 30 s overhead, floored at module default.
        per_url_budget = 90
        dynamic_timeout = min(DEFAULT_AUDIT_TIMEOUT_SECONDS, len(urls) * per_url_budget + 30)
        # Fixed argv (caller-supplied urls, no shell interpolation).
        result = subprocess.run(  # noqa: S603
            cmd,
            cwd=self.fixture_path,
            capture_output=True,
            text=True,
            timeout=dynamic_timeout,
            check=False,
        )
        if result.returncode != 0:
            stderr_tail = result.stderr[-2000:] if result.stderr else "(no stderr)"
            msg = f"axe-core cli failed (exit {result.returncode}):\n{stderr_tail}"
            raise AuditRunnerError(msg)

        reports = self._parse_axe_output(result.stdout)
        validation_error = validate_raw_axe_reports(reports)
        if validation_error is not None:
            msg = f"axe-core output failed schema validation: {validation_error}"
            raise AuditRunnerError(msg)
        return self._normalize(reports)

    @staticmethod
    def _parse_axe_output(stdout: str) -> list[dict]:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            msg = f"could not parse axe-core output: {exc}"
            raise AuditRunnerError(msg) from exc
        return payload if isinstance(payload, list) else [payload]

    @staticmethod
    def _normalize(reports: list[dict]) -> dict:
        pages = []
        total_instances = 0
        for report in reports:
            violations = report.get("violations", [])
            instance_count = sum(len(v.get("nodes", [])) for v in violations)
            total_instances += instance_count
            pages.append(
                {
                    "url": report.get("url", ""),
                    "violation_rules": sorted({v["id"] for v in violations}),
                    "violation_instance_count": instance_count,
                }
            )
        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "total_violation_instances": total_instances,
            "pages": pages,
            "raw_reports": reports,
        }

    def run(self, pages: tuple[str, ...]) -> dict:
        """Full lifecycle: start the server, audit every page, then always stop the server."""
        self.start_server()
        try:
            return self.audit_pages(pages)
        finally:
            self.stop_server()


def flatten_violation_instances(report: dict) -> list[dict]:
    """Expand a normalized report's `raw_reports` into one record per failing
    DOM node - the granularity `evaluation/benchmark_cases.json` and
    `cli.py run` actually need, since each node is one fixable unit of work
    (a page's per-rule summary in `report["pages"]` is not enough on its own:
    it drops the `selector`/`html` needed to target a specific element).
    """
    instances = []
    for raw in report.get("raw_reports", []):
        url = raw.get("url", "")
        for violation in raw.get("violations", []):
            for node in violation.get("nodes", []):
                instances.append(
                    {
                        "url": url,
                        "rule": violation.get("id", ""),
                        "wcag_tags": violation.get("tags", []),
                        "selector": ",".join(node.get("target", [])),
                        "html": node.get("html", ""),
                        "failure_summary": node.get("failureSummary"),
                    }
                )
    return instances
