#!/usr/bin/env python3
"""Local HITL review server: serves the dashboard and exposes a real
approve/reject API backed by `python -m a11y_fixer.cli review`.

Why this exists: dashboard/hitl_queue/index.html used to be a static page
that (a) could only discover tickets via a directory-listing fetch that
doesn't work over file:// and (b) only recorded Approve/Reject clicks in
browser localStorage - it never actually delivered anything. This server
gives the dashboard a real backend: GET /api/tickets lists the actual
pending queue, and POST /api/review shells out to the same `cli.py review`
command the CLI docs recommend, so a click really creates/merges a PR or
files a rejection lesson.

Run from an ACTIVATED VENV (needs the a11y_fixer package + its deps):

    cd cmu-capstone/agent
    source ../CMU/bin/activate    # or wherever your venv lives
    python hitl_server.py         # add --port 8000 to change the port

Then open http://127.0.0.1:8000/hitl_queue/ in a browser.

Stdlib only - no new dependencies to install.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

AGENT_ROOT = Path(__file__).resolve().parent
DASHBOARD_ROOT = AGENT_ROOT.parent / "dashboard"
HITL_QUEUE_DIR = AGENT_ROOT / "hitl_queue"
DECISION_SUFFIX = ".decision.json"


def _pending_tickets() -> list[dict]:
    """Mirror ReviewQueue.list_pending()'s filtering: every *.json in
    hitl_queue/ that isn't itself a *.decision.json file and doesn't have
    one sitting next to it (i.e. hasn't been reviewed yet)."""
    if not HITL_QUEUE_DIR.exists():
        return []
    tickets = []
    for path in sorted(HITL_QUEUE_DIR.glob("*.json")):
        if path.name.endswith(DECISION_SUFFIX):
            continue
        if path.with_suffix(DECISION_SUFFIX).exists():
            continue  # already reviewed
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            data = {"error": f"failed to parse {path.name}: {exc}"}
        data["filename"] = path.name
        tickets.append(data)
    return tickets


def _run_review(
    filename: str, action: str, reviewer: str, notes: str, live: bool | None
) -> dict:
    """Shell out to the real, tested CLI path rather than re-implementing
    ReviewQueue.review() here - guarantees identical behavior to what
    memory/HITL-QUEUE-SYNC-GUIDE.md documents as the correct way to do this."""
    cmd = [sys.executable, "-m", "a11y_fixer.cli", "review", filename]
    cmd.append("--approve" if action == "approve" else "--reject")
    cmd += ["--reviewer", reviewer or "dashboard"]
    if notes:
        cmd += ["--notes", notes]
    if live is True:
        cmd.append("--live")
    elif live is False:
        cmd.append("--no-live")

    proc = subprocess.run(  # noqa: S603
        cmd, cwd=AGENT_ROOT, capture_output=True, text=True, timeout=180, check=False
    )
    output: dict = {
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "ok": proc.returncode == 0,
    }
    # _cmd_review's last line of stdout is json.dumps(result, indent=2) -
    # pull out the last top-level JSON object rather than assuming layout.
    try:
        start = proc.stdout.rindex("{")
        output["result"] = json.loads(proc.stdout[start:])
    except Exception:  # noqa: BLE001
        output["result"] = None
    return output


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DASHBOARD_ROOT), **kwargs)

    def log_message(self, fmt: str, *args) -> None:  # noqa: A002
        sys.stderr.write("[hitl_server] " + (fmt % args) + "\n")

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if urlparse(self.path).path == "/api/tickets":
            self._send_json(200, {"tickets": _pending_tickets()})
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/api/review":
            self.send_error(404)
            return

        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._send_json(400, {"ok": False, "error": f"bad request body: {exc}"})
            return

        filename = body.get("filename")
        action = body.get("action")
        if not filename or action not in ("approve", "reject"):
            self._send_json(
                400,
                {
                    "ok": False,
                    "error": "filename and action ('approve'|'reject') are required",
                },
            )
            return

        reviewer = body.get("reviewer") or "dashboard"
        notes = body.get("notes") or ""
        live = body.get("live", None)  # True | False | None

        try:
            result = _run_review(filename, action, reviewer, notes, live)
        except subprocess.TimeoutExpired:
            self._send_json(
                504, {"ok": False, "error": "review command timed out after 180s"}
            )
            return
        except Exception as exc:  # noqa: BLE001
            self._send_json(500, {"ok": False, "error": str(exc)})
            return

        self._send_json(200 if result["ok"] else 500, result)


def main() -> None:
    parser = argparse.ArgumentParser(description="Local HITL review server")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if not (AGENT_ROOT / "src" / "a11y_fixer" / "cli.py").exists():
        print(  # noqa: T201
            f"warning: expected to find src/a11y_fixer/cli.py under {AGENT_ROOT} - "
            "run this from cmu-capstone/agent/",
            file=sys.stderr,
        )
    if not DASHBOARD_ROOT.exists():
        print(  # noqa: T201
            f"warning: dashboard directory not found at {DASHBOARD_ROOT}",
            file=sys.stderr,
        )

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"HITL review server running at http://127.0.0.1:{args.port}/hitl_queue/")  # noqa: T201
    print(f"  serving dashboard from: {DASHBOARD_ROOT}")  # noqa: T201
    print(f"  queue directory:        {HITL_QUEUE_DIR}")  # noqa: T201
    print("  Ctrl+C to stop")  # noqa: T201
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
