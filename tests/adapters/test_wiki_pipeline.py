from __future__ import annotations

from pathlib import Path

import pytest

from a11y_fixer.adapters.retrieval import wiki_pipeline as wp


def test_init_wiki_creates_lessons_dir(tmp_path: Path) -> None:
    wp.init_wiki(tmp_path)
    assert (tmp_path / "lessons").is_dir()


def test_init_wiki_creates_memory_file_even_with_no_lessons(tmp_path: Path) -> None:
    wp.init_wiki(tmp_path)
    memory_file = wp.memory_file_path(tmp_path)
    assert memory_file.is_file()
    assert "No HITL rejection lessons recorded yet" in memory_file.read_text(encoding="utf-8")


def test_ingest_lesson_regenerates_memory_file(tmp_path: Path) -> None:
    wp.ingest_lesson(
        tmp_path,
        rule="color-contrast",
        file_path="product.component.html",
        rejection_reason="darkened CTA broke brand palette",
        constraint="prefer text color adjustments over background changes for CTAs",
    )
    content = wp.memory_file_path(tmp_path).read_text(encoding="utf-8")
    assert "color-contrast" in content
    assert "prefer text color adjustments" in content


def test_load_lessons_on_missing_wiki_dir_returns_empty(tmp_path: Path) -> None:
    assert wp.load_lessons(tmp_path / "does-not-exist") == []


def test_ingest_lesson_persists_json_file(tmp_path: Path) -> None:
    lesson = wp.ingest_lesson(
        tmp_path,
        rule="color-contrast",
        file_path="src/app/pages/product/product.component.html",
        rejection_reason="fix darkened the CTA button below brand guidelines",
        constraint="prefer adjusting text color over background color for CTAs",
    )
    files = list((tmp_path / "lessons").glob("*.json"))
    assert len(files) == 1
    assert files[0].name.startswith(lesson.id)


def test_load_lessons_returns_chronological_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ticks = iter([100, 200, 300])
    monkeypatch.setattr(wp.time, "time_ns", lambda: next(ticks))

    wp.ingest_lesson(tmp_path, rule="rule-a", file_path="a.html", rejection_reason="r1", constraint="c1")
    wp.ingest_lesson(tmp_path, rule="rule-b", file_path="b.html", rejection_reason="r2", constraint="c2")
    wp.ingest_lesson(tmp_path, rule="rule-c", file_path="c.html", rejection_reason="r3", constraint="c3")

    lessons = wp.load_lessons(tmp_path)
    assert [lesson.rule for lesson in lessons] == ["rule-a", "rule-b", "rule-c"]


def test_query_lessons_returns_relevant_lesson(tmp_path: Path) -> None:
    wp.ingest_lesson(
        tmp_path,
        rule="color-contrast",
        file_path="product.component.html",
        rejection_reason="darkened CTA broke brand palette",
        constraint="prefer text color adjustments over background changes for CTAs",
    )
    wp.ingest_lesson(
        tmp_path,
        rule="image-alt",
        file_path="about.component.html",
        rejection_reason="alt text described pixels not meaning",
        constraint="describe semantic intent, not visual appearance",
    )

    results = wp.query_lessons(tmp_path, "color contrast CTA brand", top_k=1)

    assert len(results) == 1
    assert results[0].rule == "color-contrast"


def test_query_lessons_on_empty_wiki_returns_empty(tmp_path: Path) -> None:
    assert wp.query_lessons(tmp_path, "anything") == []
