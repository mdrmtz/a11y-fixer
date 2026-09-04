"""Wiki (institutional memory) pipeline: init/ingest/query over HITL rejection
lessons. Never authoritative for WCAG content - `wcag-mcp` is the live source
of truth for that; this module only stores and retrieves lessons learned from
past human rejections, keyed by rule/file context.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from a11y_fixer.adapters.retrieval.semantic_search import DEFAULT_TOP_K, Chunk, embed, retrieve_mmr

LESSONS_SUBDIR = "lessons"
MEMORY_FILE_NAME = "AGENTS.md"


@dataclass(frozen=True)
class Lesson:
    id: str
    rule: str
    file_path: str
    rejection_reason: str
    constraint: str
    created_at: str


def memory_file_path(wiki_dir: Path) -> Path:
    """The single aggregate file `MemoryMiddleware` loads.

    `MemoryMiddleware.sources` downloads each entry as one file - it does not
    expand a directory - so this file, not `wiki/lessons/`, is what
    `deep_agent.py` points `memory=[...]` at.
    """
    return wiki_dir / MEMORY_FILE_NAME


def _rebuild_memory_file(wiki_dir: Path) -> None:
    lessons = load_lessons(wiki_dir)
    if not lessons:
        body = "# Institutional Memory\n\nNo HITL rejection lessons recorded yet.\n"
    else:
        entries = "\n\n".join(
            f"## {lesson.rule} - {lesson.file_path}\n"
            f"- Rejected because: {lesson.rejection_reason}\n"
            f"- Constraint for next time: {lesson.constraint}"
            for lesson in lessons
        )
        body = f"# Institutional Memory\n\n{entries}\n"
    memory_file_path(wiki_dir).write_text(body, encoding="utf-8")


def init_wiki(wiki_dir: Path) -> None:
    """Create the wiki directory structure if it doesn't already exist."""
    (wiki_dir / LESSONS_SUBDIR).mkdir(parents=True, exist_ok=True)
    if not memory_file_path(wiki_dir).exists():
        _rebuild_memory_file(wiki_dir)


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "lesson"


def _lesson_text(lesson: Lesson) -> str:
    return f"{lesson.rule} {lesson.file_path} {lesson.rejection_reason} {lesson.constraint}"


def ingest_lesson(wiki_dir: Path, *, rule: str, file_path: str, rejection_reason: str, constraint: str) -> Lesson:
    """Persist one HITL "reject + lesson" as a JSON file under `wiki/lessons/`.

    Filenames are prefixed with `time.time_ns()` so a lexicographic glob sort
    is also chronological order - sorting by a random UUID filename is not.
    """
    init_wiki(wiki_dir)
    lesson_id = f"{time.time_ns()}-{_slugify(rule)}"
    lesson = Lesson(
        id=lesson_id,
        rule=rule,
        file_path=file_path,
        rejection_reason=rejection_reason,
        constraint=constraint,
        created_at=datetime.now(UTC).isoformat(),
    )
    path = wiki_dir / LESSONS_SUBDIR / f"{lesson_id}.json"
    path.write_text(json.dumps(asdict(lesson), indent=2), encoding="utf-8")
    _rebuild_memory_file(wiki_dir)
    return lesson


def load_lessons(wiki_dir: Path) -> list[Lesson]:
    """Load every stored lesson, oldest first (filename-prefix chronological order)."""
    lessons_dir = wiki_dir / LESSONS_SUBDIR
    if not lessons_dir.exists():
        return []
    return [Lesson(**json.loads(path.read_text(encoding="utf-8"))) for path in sorted(lessons_dir.glob("*.json"))]


def query_lessons(wiki_dir: Path, query: str, top_k: int = DEFAULT_TOP_K) -> list[Lesson]:
    """MMR-reranked lesson lookup: diverse, relevant lessons for a rule/context."""
    lessons = load_lessons(wiki_dir)
    if not lessons:
        return []
    chunks = [Chunk(id=lesson.id, text=_lesson_text(lesson), vector=embed(_lesson_text(lesson))) for lesson in lessons]
    selected = retrieve_mmr(query, chunks, top_k=top_k)
    by_id = {lesson.id: lesson for lesson in lessons}
    return [by_id[chunk.id] for chunk in selected]
