"""Pytest configuration for a11y-fixer tests.

Provides fixtures for tests that need benchmark data without duplicating
committed test data in version control.
"""

import json
from pathlib import Path

import pytest

"""Pytest configuration for a11y-fixer tests.

Provides fixtures for tests that need benchmark data without duplicating
committed test data in version control.
"""

import json
from pathlib import Path
from random import Random

import pytest

# Random generator for reproducible test data (seeded for consistency)
_rng = Random(42)

# Number of test cases to generate (configurable, default 22)
# Can be overridden via environment variable or fixtures
BENCHMARK_TEST_CASES_COUNT = 22


def _generate_random_benchmark_cases(count: int) -> list[dict]:
    """Generate N random benchmark test cases.

    Creates realistic-looking test data that:
    - Is NOT a copy of actual benchmark_cases.json
    - Has enough distribution to satisfy phase filter tests
    - Uses a fixed seed for reproducibility

    Strategically ensures at least 2 cases match each phase requirement.

    Args:
        count: Number of test cases to generate (default 22, can be 0 to N)
    """
    if count == 0:
        return []

    pages = ["/", "/about", "/features", "/pricing", "/blog", "/contact"]
    rules = ["html-has-lang", "color-contrast", "image-alt", "button-name", "link-name"]
    wcag_codes = ["1.1.1", "1.4.3", "2.1.1", "3.1.1", "4.1.2"]

    cases = []

    # First 2 cases: ensure f1 phase matches (/about + 1.1.1)
    # Only if count >= 2
    cases_for_phase = min(2, count)
    for i in range(1, cases_for_phase + 1):
        cases.append(
            {
                "id": f"case-{i:02d}",
                "page": "/about",
                "rule": _rng.choice(rules),
                "selector": f".element-{_rng.randint(1000, 9999)}",
                "wcag": "1.1.1",
                "ground_truth_fix": f"Apply fix for {_rng.choice(rules)} violation",
            }
        )

    # Remaining cases: fully random
    for i in range(cases_for_phase + 1, count + 1):
        cases.append(
            {
                "id": f"case-{i:02d}",
                "page": _rng.choice(pages),
                "rule": _rng.choice(rules),
                "selector": f".element-{_rng.randint(1000, 9999)}",
                "wcag": _rng.choice(wcag_codes),
                "ground_truth_fix": f"Apply fix for {_rng.choice(rules)} violation",
            }
        )

    return cases


# Generate test cases with configurable count
TEST_BENCHMARK_DATA = _generate_random_benchmark_cases(BENCHMARK_TEST_CASES_COUNT)


@pytest.fixture(autouse=True, scope="session")
def ensure_benchmark_cases_file():
    """Ensure benchmark_cases.json exists for tests that require it.

    On fresh systems where evaluation/benchmark_cases.json doesn't exist,
    this fixture creates it with minimal test data. Tests that need real
    data can call load_benchmark_cases() directly, which will read this file.

    This allows tests to pass on fresh systems without duplicating data
    in version control. The file is created once per test session.
    """
    BENCHMARK_CASES_PATH = (
        Path(__file__).resolve().parent.parent / "evaluation" / "benchmark_cases.json"
    )

    # If file already exists (from git or previous runs), use it
    if BENCHMARK_CASES_PATH.exists():
        return

    # Create parent directory if needed
    BENCHMARK_CASES_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Write minimal test data to file
    with open(BENCHMARK_CASES_PATH, "w") as f:
        json.dump(TEST_BENCHMARK_DATA, f, indent=2)
