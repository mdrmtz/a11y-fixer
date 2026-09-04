from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from a11y_fixer.adapters.audit_runner import AuditRunnerError, AxeAuditRunner, flatten_violation_instances


class _FakeProcess:
    def __init__(self, *, poll_value: int | None = None, stdout_text: str = "") -> None:
        self._poll_value = poll_value
        self.stdout = MagicMock()
        self.stdout.read.return_value = stdout_text
        self.returncode = poll_value
        self.terminate = MagicMock()
        self.kill = MagicMock()
        self.wait = MagicMock()

    def poll(self) -> int | None:
        return self._poll_value


@pytest.fixture
def runner(tmp_path: Path) -> AxeAuditRunner:
    return AxeAuditRunner(fixture_path=tmp_path, startup_timeout_seconds=0.3)


def test_start_server_raises_if_fixture_missing(tmp_path: Path) -> None:
    runner = AxeAuditRunner(fixture_path=tmp_path / "does-not-exist")
    with pytest.raises(AuditRunnerError, match="does not exist"):
        runner.start_server()


def test_start_server_reuses_already_open_port(runner: AxeAuditRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("a11y_fixer.adapters.audit_runner._is_port_open", lambda host, port: True)  # noqa: ARG005
    popen_mock = MagicMock()
    monkeypatch.setattr(subprocess, "Popen", popen_mock)

    runner.start_server()

    popen_mock.assert_not_called()


def test_wait_for_server_raises_on_early_process_exit(runner: AxeAuditRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("a11y_fixer.adapters.audit_runner._is_port_open", lambda host, port: False)  # noqa: ARG005
    fake = _FakeProcess(poll_value=1, stdout_text="Error: something exploded")
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: fake)  # noqa: ARG005

    with pytest.raises(AuditRunnerError, match="exited early"):
        runner.start_server()


def test_wait_for_server_times_out(runner: AxeAuditRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("a11y_fixer.adapters.audit_runner._is_port_open", lambda host, port: False)  # noqa: ARG005
    fake = _FakeProcess(poll_value=None)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: fake)  # noqa: ARG005

    with pytest.raises(AuditRunnerError, match="did not become ready"):
        runner.start_server()


def test_stop_server_terminates_gracefully(runner: AxeAuditRunner) -> None:
    fake = _FakeProcess(poll_value=None)
    runner._process = fake  # noqa: SLF001 - test hook

    runner.stop_server()

    fake.terminate.assert_called_once()
    fake.kill.assert_not_called()
    assert runner._process is None  # noqa: SLF001


def test_stop_server_kills_after_terminate_timeout(runner: AxeAuditRunner) -> None:
    fake = _FakeProcess(poll_value=None)
    fake.wait.side_effect = [subprocess.TimeoutExpired(cmd="ng serve", timeout=10), None]
    runner._process = fake  # noqa: SLF001

    runner.stop_server()

    fake.terminate.assert_called_once()
    fake.kill.assert_called_once()


def _axe_report(url: str, violations: list[dict]) -> dict:
    return {"url": url, "violations": violations}


def test_audit_urls_scans_arbitrary_urls_directly(runner: AxeAuditRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("a11y_fixer.adapters.audit_runner.shutil.which", lambda _: "/usr/bin/npx")
    reports = [_axe_report("https://example.com/about", [{"id": "image-alt", "nodes": [{}]}])]
    fake_result = MagicMock(returncode=0, stdout=json.dumps(reports), stderr="")
    captured_cmd: list[str] = []
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, **_k: (captured_cmd.extend(cmd), fake_result)[1]  # noqa: ARG005
    )

    normalized = runner.audit_urls(["https://example.com/about"])

    assert normalized["total_violation_instances"] == 1
    assert "https://example.com/about" in captured_cmd


def test_audit_urls_raises_if_npx_missing(runner: AxeAuditRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("a11y_fixer.adapters.audit_runner.shutil.which", lambda _: None)

    with pytest.raises(AuditRunnerError, match="npx not found"):
        runner.audit_urls(["https://example.com/"])


def test_audit_pages_delegates_to_audit_urls_with_host_port(runner: AxeAuditRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    captured_urls: list[str] = []

    def _fake_audit_urls(urls: list[str]) -> dict:
        captured_urls.extend(urls)
        return {"ok": True}

    monkeypatch.setattr(runner, "audit_urls", _fake_audit_urls)

    result = runner.audit_pages(pages=("/", "/about"))

    assert captured_urls == [f"http://{runner.host}:{runner.port}/", f"http://{runner.host}:{runner.port}/about"]
    assert result == {"ok": True}


def test_audit_pages_normalizes_list_output(runner: AxeAuditRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("a11y_fixer.adapters.audit_runner.shutil.which", lambda _: "/usr/bin/npx")
    reports = [
        _axe_report("http://127.0.0.1:4200/", [{"id": "html-has-lang", "nodes": [{}]}]),
        _axe_report(
            "http://127.0.0.1:4200/blog",
            [
                {"id": "html-has-lang", "nodes": [{}]},
                {"id": "button-name", "nodes": [{}, {}]},
            ],
        ),
    ]
    fake_result = MagicMock(returncode=0, stdout=json.dumps(reports), stderr="")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: fake_result)  # noqa: ARG005

    normalized = runner.audit_pages(pages=("/", "/blog"))

    assert normalized["total_violation_instances"] == 4
    assert normalized["pages"][1]["violation_rules"] == ["button-name", "html-has-lang"]
    assert normalized["pages"][1]["violation_instance_count"] == 3
    assert len(normalized["raw_reports"]) == 2


def test_audit_pages_normalizes_single_object_output(runner: AxeAuditRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("a11y_fixer.adapters.audit_runner.shutil.which", lambda _: "/usr/bin/npx")
    report = _axe_report("http://127.0.0.1:4200/", [{"id": "image-alt", "nodes": [{}]}])
    fake_result = MagicMock(returncode=0, stdout=json.dumps(report), stderr="")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: fake_result)  # noqa: ARG005

    normalized = runner.audit_pages(pages=("/",))

    assert normalized["total_violation_instances"] == 1
    assert normalized["pages"][0]["violation_rules"] == ["image-alt"]


def test_audit_pages_raises_on_nonzero_exit(runner: AxeAuditRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("a11y_fixer.adapters.audit_runner.shutil.which", lambda _: "/usr/bin/npx")
    fake_result = MagicMock(returncode=2, stdout="", stderr="chrome crashed")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: fake_result)  # noqa: ARG005

    with pytest.raises(AuditRunnerError, match="exit 2"):
        runner.audit_pages(pages=("/",))


def test_audit_pages_raises_on_invalid_json(runner: AxeAuditRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("a11y_fixer.adapters.audit_runner.shutil.which", lambda _: "/usr/bin/npx")
    fake_result = MagicMock(returncode=0, stdout="not json", stderr="")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: fake_result)  # noqa: ARG005

    with pytest.raises(AuditRunnerError, match="could not parse"):
        runner.audit_pages(pages=("/",))


def test_audit_pages_raises_if_npx_missing(runner: AxeAuditRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("a11y_fixer.adapters.audit_runner.shutil.which", lambda _: None)

    with pytest.raises(AuditRunnerError, match="npx not found"):
        runner.audit_pages(pages=("/",))


def test_audit_pages_raises_on_schema_invalid_report(runner: AxeAuditRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("a11y_fixer.adapters.audit_runner.shutil.which", lambda _: "/usr/bin/npx")
    reports = [{"violations": []}]  # missing required "url"
    fake_result = MagicMock(returncode=0, stdout=json.dumps(reports), stderr="")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: fake_result)  # noqa: ARG005

    with pytest.raises(AuditRunnerError, match="schema validation"):
        runner.audit_pages(pages=("/",))


def test_audit_pages_raises_on_schema_invalid_violation(runner: AxeAuditRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("a11y_fixer.adapters.audit_runner.shutil.which", lambda _: "/usr/bin/npx")
    reports = [{"url": "http://127.0.0.1:4200/", "violations": [{"nodes": []}]}]  # violation missing required "id"
    fake_result = MagicMock(returncode=0, stdout=json.dumps(reports), stderr="")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: fake_result)  # noqa: ARG005

    with pytest.raises(AuditRunnerError, match="schema validation"):
        runner.audit_pages(pages=("/",))


def test_run_starts_audits_then_always_stops(runner: AxeAuditRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(runner, "start_server", lambda: calls.append("start"))
    monkeypatch.setattr(runner, "stop_server", lambda: calls.append("stop"))

    def failing_audit(pages: tuple[str, ...]) -> dict:  # noqa: ARG001
        calls.append("audit")
        msg = "boom"
        raise AuditRunnerError(msg)

    monkeypatch.setattr(runner, "audit_pages", failing_audit)

    with pytest.raises(AuditRunnerError, match="boom"):
        runner.run(pages=("/",))

    assert calls == ["start", "audit", "stop"]


def test_flatten_violation_instances_expands_one_record_per_node() -> None:
    report = {
        "raw_reports": [
            {
                "url": "http://127.0.0.1:4200/case-studies",
                "violations": [
                    {
                        "id": "image-alt",
                        "tags": ["wcag2a", "wcag111"],
                        "nodes": [
                            {"html": "<img src='a.png'>", "target": [".hero img"], "failureSummary": "Fix: add alt"},
                            {"html": "<img src='b.png'>", "target": [".card img"], "failureSummary": "Fix: add alt"},
                        ],
                    }
                ],
            }
        ]
    }

    instances = flatten_violation_instances(report)

    assert len(instances) == 2
    assert instances[0]["rule"] == "image-alt"
    assert instances[0]["selector"] == ".hero img"
    assert instances[0]["wcag_tags"] == ["wcag2a", "wcag111"]
    assert instances[1]["selector"] == ".card img"


def test_flatten_violation_instances_on_empty_report_returns_empty() -> None:
    assert flatten_violation_instances({}) == []
