"""Selector-to-file locator for codebase_compiler subagent.

Enables deterministic file discovery for CSS selectors by combining:
- Hint-text grepping to narrow candidates (O(n) file scans)
- HTML regex heuristics to match selectors (no full CSS parser needed)
- Confidence scoring for result ranking

Fixes the 40-66% file-location failure rate from prior benchmark runs.
"""

from pathlib import Path
from typing import Optional
import re


def locate_selector_in_component(
    selector: str,
    hint_text: Optional[str] = None,
    glob_pattern: str = "src/**/*.component.html",
    codebase_root: Optional[Path] = None,
) -> list[dict]:
    """Locate all component files matching a CSS selector.

    Args:
        selector: CSS selector string (e.g., "img[src$='atlas-dashboard.svg']")
        hint_text: Specific text/value from selector to grep for (e.g., "atlas-dashboard.svg")
        glob_pattern: Glob pattern for candidate component files (default Angular convention)
        codebase_root: Root directory to search from (defaults to current directory)

    Returns:
        List of dicts sorted by confidence (descending):
        {
            "file_path": str (absolute or relative path),
            "line_number": int (1-indexed),
            "element_html": str (matched HTML element),
            "confidence": float (0.0-1.0, higher = more certain match)
        }

    Algorithm:
    1. If hint_text provided, grep for it across glob_pattern to narrow candidates
    2. For each candidate file, parse HTML and find elements matching the selector
    3. Return matches sorted by confidence (exact > partial), then by file path

    Example:
        >>> results = locate_selector_in_component(
        ...     selector="img[src$='atlas-dashboard.svg']",
        ...     hint_text="atlas-dashboard.svg",
        ...     codebase_root=Path("Hallucinate.io")
        ... )
        >>> if results:
        ...     print(f"Found at: {results[0]['file_path']}")
    """
    if codebase_root is None:
        codebase_root = Path(".")

    # Step 1: Grep for hint_text to narrow candidates
    candidates = []
    if hint_text:
        for file in codebase_root.glob(glob_pattern):
            try:
                if hint_text in file.read_text(errors="ignore"):
                    candidates.append(file)
            except (OSError, ValueError):
                # Skip files that can't be read
                continue
    else:
        try:
            candidates = list(codebase_root.glob(glob_pattern))
        except (OSError, ValueError):
            return []

    # Step 2: Validate selector against HTML
    results = []
    for file in candidates:
        try:
            html = file.read_text(errors="ignore")
            matches = _find_selector_matches(html, selector, hint_text)
            if matches:
                for match_info in matches:
                    results.append(
                        {
                            "file_path": str(file),
                            "line_number": match_info["line"],
                            "element_html": match_info["html"],
                            "confidence": match_info["confidence"],
                        }
                    )
        except (OSError, ValueError):
            # Skip files that can't be read
            continue

    # Sort by confidence (descending), then by file path
    results.sort(key=lambda x: (-x["confidence"], x["file_path"]))
    return results


def _find_selector_matches(
    html: str, selector: str, hint_text: Optional[str] = None
) -> list[dict]:
    """Parse HTML and find elements matching the CSS selector.

    Uses heuristic approach (no full CSS parser):
    - Extract tag name from selector (e.g., "img" from "img[src$='...']")
    - Extract attribute selectors (e.g., [src$="value"])
    - Search for matching tag + attribute in HTML lines
    - Score confidence based on tag + attribute matches

    Args:
        html: HTML content as string
        selector: CSS selector to match against
        hint_text: Original hint text (for confidence boosting)

    Returns:
        List of dicts with matching elements:
        {
            "line": int (1-indexed line number),
            "html": str (matched element HTML),
            "confidence": float (0.5-1.0)
        }
    """
    matches = []

    # Extract the tag name from the selector
    # For compound selectors like "article:nth-child(2) img", get the rightmost tag
    # For simple selectors like "img[src='...']", get the tag
    # Split by space to handle descendant combinator
    selector_parts = selector.split()
    tag_name = None

    if selector_parts:
        # Get the last part (rightmost tag)
        last_part = selector_parts[-1]
        tag_match = re.match(r"(\w+)", last_part)
        tag_name = tag_match.group(1) if tag_match else None

    if not tag_name:
        return []

    # Extract attribute selectors [attr="value"] or [attr$="value"]
    # Patterns: [attr="value"], [attr$="value"], [attr*="value"], etc.
    # Support both single and double quotes
    attr_pattern = r"\[(\w+)([~*$|^]?)=['\"]([^'\"]+)['\"]\]"
    attrs = re.findall(attr_pattern, selector)

    # Search for matching tag + attribute in HTML
    tag_regex = f"<{tag_name}[^>]*>"
    for line_num, line in enumerate(html.split("\n"), 1):
        if re.search(tag_regex, line, re.IGNORECASE):
            # Base confidence: tag matched
            confidence = 0.5

            # Boost confidence for each attribute that matches
            for attr_name, op, attr_value in attrs:
                if attr_value in line:
                    confidence = min(1.0, confidence + 0.25)

            # Report if: (1) we have attributes and they matched, or (2) selector has no attributes
            # When selector has no attributes, even tag-only match (confidence=0.5) is valid
            should_report = (len(attrs) == 0) or (confidence > 0.5)

            if should_report:
                element_match = re.search(tag_regex, line, re.IGNORECASE)
                if element_match:
                    matches.append(
                        {
                            "line": line_num,
                            "html": element_match.group(0),
                            "confidence": confidence,
                        }
                    )

    return matches
