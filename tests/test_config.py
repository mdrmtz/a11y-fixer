from __future__ import annotations

from pathlib import Path

import pytest

from a11y_fixer import config


def test_to_virtual_path_is_root_relative_with_leading_slash() -> None:
    virtual = config.to_virtual_path(config.fixture_path())
    assert virtual == "/Hallucinate.io"


def test_to_virtual_path_nested_path() -> None:
    virtual = config.to_virtual_path(config.skills_dir() / "a11y-fixer")
    assert virtual == "/.agents/skills/a11y-fixer"


def test_to_virtual_path_rejects_path_outside_repo_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not in the subpath"):
        config.to_virtual_path(tmp_path)


def test_selected_llm_backend_defaults_to_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("A11Y_LLM_BACKEND", raising=False)
    monkeypatch.delenv("A11Y_LLM_MODEL", raising=False)
    assert config.selected_llm_backend() == config.LLMBackendConfig(backend="ollama", model="ollama:llama3.1")


def test_selected_llm_backend_bare_model_override_gets_backend_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bare model id (no `provider:` prefix) must be combined with the
    selected backend - passing it through unprefixed makes `init_chat_model`
    fail to infer a provider (found via a real `openrouter/free` run)."""
    monkeypatch.setenv("A11Y_LLM_BACKEND", "openrouter")
    monkeypatch.setenv("A11Y_LLM_MODEL", "openrouter/free")
    assert config.selected_llm_backend().model == "openrouter:openrouter/free"


def test_selected_llm_backend_bare_model_override_with_embedded_colon(monkeypatch: pytest.MonkeyPatch) -> None:
    """OpenRouter ids can themselves contain a colon (e.g. a `:free` suffix) -
    that must not be mistaken for an already-complete `provider:model` spec
    (found via a real `google/gemma-4-26b-a4b-it:free` run)."""
    monkeypatch.setenv("A11Y_LLM_BACKEND", "openrouter")
    monkeypatch.setenv("A11Y_LLM_MODEL", "google/gemma-4-26b-a4b-it:free")
    assert config.selected_llm_backend().model == "openrouter:google/gemma-4-26b-a4b-it:free"


def test_selected_llm_backend_full_spec_override_used_as_is(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("A11Y_LLM_BACKEND", "openrouter")
    monkeypatch.setenv("A11Y_LLM_MODEL", "openrouter:nvidia/nemotron-3-ultra-550b-a55b")
    assert config.selected_llm_backend().model == "openrouter:nvidia/nemotron-3-ultra-550b-a55b"


def test_selected_llm_backend_rejects_unknown_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("A11Y_LLM_BACKEND", "bogus")
    with pytest.raises(ValueError, match="Unknown A11Y_LLM_BACKEND"):
        config.selected_llm_backend()


def test_is_dev_checkout_true_in_this_repo() -> None:
    """Sanity check: running the real test suite from the real checkout
    must take the dev-mode branch - every other test in this file (and in
    test_subagents.py) that calls `skills_dir()`/`fixture_path()`/
    `repo_root()` without monkeypatching relies on this being true."""
    assert config._is_dev_checkout() is True


def test_workspace_root_honors_a11y_home_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("A11Y_HOME", str(tmp_path))
    assert config.workspace_root() == tmp_path.resolve()


def test_workspace_root_defaults_to_dot_a11y_fixer_under_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("A11Y_HOME", raising=False)
    monkeypatch.chdir(tmp_path)
    assert config.workspace_root() == tmp_path / ".a11y-fixer"


def test_repo_root_and_agent_root_collapse_to_workspace_root_outside_dev_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Outside the dev checkout there's no `agent/` subfolder of a repo
    root to distinguish - both helpers must resolve to the same workspace,
    since that's now the deep agent's single sandbox root (`to_virtual_
    path()`/`FilesystemBackend`)."""
    monkeypatch.setattr(config, "_is_dev_checkout", lambda: False)
    monkeypatch.setenv("A11Y_HOME", str(tmp_path))
    assert config.repo_root() == tmp_path.resolve()
    assert config.agent_root() == tmp_path.resolve()


def test_resolve_skill_is_passthrough_to_skills_dir_in_dev_checkout() -> None:
    assert config.resolve_skill("a11y-fixer") == config.skills_dir() / "a11y-fixer"


def test_resolve_skill_materializes_bundled_copy_outside_dev_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Outside the dev checkout, `resolve_skill()` must copy the package's
    own bundled `a11y_fixer/skills/<name>/` data into the workspace, since
    `deepagents`' `SkillsMiddleware` reads skill content exclusively
    through the `FilesystemBackend` rooted at `repo_root()` - a path under
    site-packages (where the bundled copy actually lives) is never
    reachable through that sandbox root."""
    # Capture the real dev-mode content *before* patching `_is_dev_checkout`
    # - `skills_dir()` itself calls `repo_root()`, so evaluating it after the
    # patch would compare the fake workspace against itself instead of
    # against the real bundled source.
    expected = (config.skills_dir() / "a11y-fixer" / "SKILL.md").read_text(encoding="utf-8")

    monkeypatch.setattr(config, "_is_dev_checkout", lambda: False)
    monkeypatch.setenv("A11Y_HOME", str(tmp_path))

    resolved = config.resolve_skill("a11y-fixer")

    assert resolved == tmp_path.resolve() / ".skills" / "a11y-fixer"
    assert (resolved / "SKILL.md").read_text(encoding="utf-8") == expected


def test_resolve_skill_raises_clearly_for_unknown_bundled_skill(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(config, "_is_dev_checkout", lambda: False)
    monkeypatch.setenv("A11Y_HOME", str(tmp_path))
    with pytest.raises(FileNotFoundError, match="no bundled skill"):
        config.resolve_skill("not-a-real-skill")
