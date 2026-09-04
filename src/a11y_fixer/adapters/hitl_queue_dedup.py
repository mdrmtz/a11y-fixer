"""HITL queue deduplication: clean up existing duplicate entries.

This utility scans the hitl_queue/ directory, identifies duplicates
(same rule_id + selector), and keeps only the most recent entry for each.

Duplicates are moved to a .stale/ subdirectory for audit trail purposes.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Optional


def deduplicate_hitl_queue(
    queue_dir: Path,
    dry_run: bool = False,
    stale_dir: Optional[Path] = None,
) -> dict:
    """Deduplicate HITL queue entries.

    Args:
        queue_dir: Path to hitl_queue/ directory
        dry_run: If True, report what would be done without making changes
        stale_dir: Path to move stale entries to (default: queue_dir/.stale/)

    Returns:
        Summary dict with counts:
        - total_entries: Total queue files found
        - duplicates: Number of duplicate entries removed
        - stale_dir_path: Path where stale entries were moved
    """
    if not queue_dir.exists():
        return {"total_entries": 0, "duplicates": 0, "stale_dir_path": None}

    if stale_dir is None:
        stale_dir = queue_dir / ".stale"

    # Group entries by rule_id + selector
    groups: dict[str, list[dict]] = defaultdict(list)

    for queue_file in sorted(queue_dir.glob("*.json")):
        if queue_file.name.endswith(".decision.json") or queue_file.name == "index.html":
            continue

        try:
            data = json.loads(queue_file.read_text(encoding="utf-8"))
            violation = data.get("violation", {})
            rule_id = violation.get("rule", "unknown")
            selector = violation.get("selector", "unknown")
            key = f"{rule_id}|{selector}"

            # Extract timestamp from filename (format: {timestamp}-{slug}.json)
            try:
                timestamp = int(queue_file.name.split("-")[0])
            except (ValueError, IndexError):
                timestamp = 0

            groups[key].append(
                {
                    "path": queue_file,
                    "timestamp": timestamp,
                    "rule_id": rule_id,
                    "selector": selector,
                }
            )
        except (json.JSONDecodeError, KeyError):
            # Skip malformed entries
            continue

    # Identify duplicates: keep newest, mark rest as stale
    total_entries = sum(len(entries) for entries in groups.values())
    stale_count = 0

    for key, entries in groups.items():
        if len(entries) > 1:
            # Sort by timestamp descending (newest first)
            entries.sort(key=lambda e: e["timestamp"], reverse=True)

            # Keep the first (newest), mark rest as stale
            for stale_entry in entries[1:]:
                stale_count += 1
                old_path = stale_entry["path"]

                if not dry_run:
                    # Create stale directory if it doesn't exist
                    stale_dir.mkdir(parents=True, exist_ok=True)

                    # Move stale entry
                    stale_path = stale_dir / old_path.name
                    old_path.rename(stale_path)

                    print(
                        f"  Moved {old_path.name} → {stale_dir.name}/{old_path.name}"
                    )
                else:
                    print(f"  [DRY RUN] Would move: {old_path.name} → .stale/")

    return {
        "total_entries": total_entries,
        "unique_violations": len(groups),
        "duplicates_removed": stale_count,
        "stale_dir_path": str(stale_dir),
    }


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Clean up duplicate HITL queue entries"
    )
    parser.add_argument(
        "--queue-dir",
        type=Path,
        help="Path to hitl_queue/ directory",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )

    args = parser.parse_args()

    # Determine queue directory
    if args.queue_dir:
        queue_dir = args.queue_dir
    else:
        # Default to hitl_queue/ in current agent directory
        queue_dir = Path.cwd() / "hitl_queue"

    if not queue_dir.exists():
        print(f"Error: Queue directory not found: {queue_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Deduplicating HITL queue: {queue_dir}")
    if args.dry_run:
        print("(DRY RUN MODE - no changes will be made)\n")

    result = deduplicate_hitl_queue(queue_dir, dry_run=args.dry_run)

    print(f"\nSummary:")
    print(f"  Total entries: {result['total_entries']}")
    print(f"  Unique violations: {result['unique_violations']}")
    print(f"  Duplicates removed: {result['duplicates_removed']}")
    print(f"  Stale directory: {result['stale_dir_path']}")

    if not args.dry_run and result["duplicates_removed"] > 0:
        print(
            f"\n✅ Removed {result['duplicates_removed']} duplicate queue entries"
        )
