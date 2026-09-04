"""Real end-to-end test: starts `ng serve` against the Hallucinate.io fixture and
runs the actual `@axe-core/cli` binary. Requires npm dependencies to be installed
in the submodule. Skipped by default (`pytest tests/` runs with `-m "not e2e"`);
run explicitly with `pytest tests/e2e/ -m e2e`.
"""

from __future__ import annotations

import pytest

from a11y_fixer import config
from a11y_fixer.adapters.audit_runner import AxeAuditRunner

pytestmark = pytest.mark.e2e

# The 11 routes registered in Hallucinate.io/src/app/app.routes.ts. This
# used to be `adapters.audit_runner.DEFAULT_PAGES`, but that hardcoded,
# Hallucinate.io-specific default was removed from production code (the
# CLI now always discovers routes via `audit_crawler.discover_and_audit()`
# instead of defaulting to this fixture's own route list). This benchmark
# is reconciled against exactly these 11 real routes, so the literal list
# is kept here, test-local, rather than swapped for a generic "/"-only
# fallback that would silently audit a fraction of the fixture.
HALLUCINATE_IO_ROUTES: tuple[str, ...] = (
    "/",
    "/home",
    "/product",
    "/case-studies",
    "/docs",
    "/careers",
    "/blog",
    "/pricing",
    "/about",
    "/contact",
    "/status",
)


def test_full_audit_matches_reconciled_benchmark() -> None:
    """Reconciles against the ground-truth benchmark in agent-plan.md.

    The plan's "18 violation instances" counts distinct (page, rule) pairs;
    this adapter counts individual failing DOM nodes (the unit `evaluation/
    benchmark_cases.json` actually needs, since each node gets its own fix).
    A live run (2026-08-31, axe-core 4.13, `--tags wcag2a,wcag2aa`) confirms:
    18 (page, rule) pairs across 5 rules on 11 pages, totalling 22 DOM-node
    instances (some pages have >1 failing node per rule, e.g. multiple
    unlabeled images on the same page).
    """
    runner = AxeAuditRunner(fixture_path=config.fixture_path(), port=4288)

    report = runner.run(pages=HALLUCINATE_IO_ROUTES)

    assert len(report["pages"]) == 11
    assert report["total_violation_instances"] == 22
    page_rule_pairs = sum(len(page["violation_rules"]) for page in report["pages"])
    assert page_rule_pairs == 18
    rules_seen = {rule for page in report["pages"] for rule in page["violation_rules"]}
    assert rules_seen == {"html-has-lang", "color-contrast", "image-alt", "button-name", "link-name"}
    assert all("html-has-lang" in page["violation_rules"] for page in report["pages"])
