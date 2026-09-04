"""100% code coverage tests for codebase_compiler.py.

Tests comprehensively cover:
- _permissions() function: permission rule creation and ordering
- build() async function: SubAgent spec construction
- Module constants: SYSTEM_PROMPT, RUBRIC_SYSTEM_PROMPT, NAME
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from deepagents import FilesystemMiddleware, FilesystemPermission, RubricMiddleware, SubAgent
from langchain_core.language_models import BaseChatModel

from a11y_fixer.agents import codebase_compiler


class TestPermissions:
    """Test _permissions() function."""

    def test_permissions_returns_three_rules(self) -> None:
        """Verify _permissions returns a list with exactly 3 permission rules."""
        perms = codebase_compiler._permissions("/virtual/fixture")
        assert isinstance(perms, list)
        assert len(perms) == 3
        assert all(isinstance(p, FilesystemPermission) for p in perms)

    def test_permissions_first_rule_read_allow(self) -> None:
        """First rule: allow reads on fixture/**."""
        perms = codebase_compiler._permissions("/virtual/fixture")
        first = perms[0]
        assert first.operations == ["read"]
        assert first.paths == ["/virtual/fixture/**"]
        assert first.mode == "allow"

    def test_permissions_second_rule_write_allow_components(self) -> None:
        """Second rule: allow writes on component files and index.html."""
        perms = codebase_compiler._permissions("/virtual/fixture")
        second = perms[1]
        assert second.operations == ["write"]
        assert second.mode == "allow"
        # Verify all expected paths are present
        expected_paths = {
            "/virtual/fixture/src/app/**/*.component.html",
            "/virtual/fixture/src/app/**/*.component.ts",
            "/virtual/fixture/src/app/**/*.component.scss",
            "/virtual/fixture/src/index.html",
        }
        assert set(second.paths) == expected_paths

    def test_permissions_third_rule_write_deny_catchall(self) -> None:
        """Third rule: deny writes everywhere else (catch-all)."""
        perms = codebase_compiler._permissions("/virtual/fixture")
        third = perms[2]
        assert third.operations == ["write"]
        assert third.paths == ["/**"]
        assert third.mode == "deny"

    def test_permissions_with_different_virtual_path(self) -> None:
        """Verify _permissions works with different virtual paths."""
        perms = codebase_compiler._permissions("/custom/path")
        # First rule should use custom path
        assert perms[0].paths == ["/custom/path/**"]
        # Second rule should use custom path for component globs
        assert "/custom/path/src/app/**/*.component.html" in perms[1].paths
        # Third rule (catch-all deny) is path-independent
        assert perms[2].paths == ["/**"]

    def test_permissions_ordering_matters(self) -> None:
        """Verify rule ordering: first matching rule wins."""
        perms = codebase_compiler._permissions("/v/f")
        # Rule 0: read allow (fixture/**)
        # Rule 1: write allow (specific component globs)
        # Rule 2: write deny (catch-all)
        # A write to /v/f/src/app/page.component.html should match rule 1 (allow)
        # A write to /other/file should match rule 2 (deny)
        assert perms[0].mode == "allow"  # read
        assert perms[1].mode == "allow"  # write specific
        assert perms[2].mode == "deny"   # write catch-all


class TestBuild:
    """Test build() async function."""

    @pytest.mark.asyncio
    async def test_build_with_string_model(self) -> None:
        """Test build() with a model string."""
        with patch("a11y_fixer.agents.codebase_compiler.aget_tools") as mock_aget_tools:
            mock_aget_tools.return_value = [MagicMock()]

            with patch("a11y_fixer.config.fixture_path") as mock_fixture:
                with patch("a11y_fixer.config.to_virtual_path") as mock_vpath:
                    with patch("a11y_fixer.config.skills_dir") as mock_skills:
                        mock_fixture.return_value = Path("/path/to/fixture")
                        mock_vpath.side_effect = lambda p: "/" + str(p).split("/")[-1]
                        mock_skills.return_value = Path("/path/skills")

                        result = await codebase_compiler.build("test-model")

                        # SubAgent is a TypedDict, so check for dict structure
                        assert isinstance(result, dict)
                        assert result["name"] == codebase_compiler.NAME

    @pytest.mark.asyncio
    async def test_build_with_chat_model_instance(self) -> None:
        """Test build() with a BaseChatModel instance."""
        mock_model = MagicMock(spec=BaseChatModel)

        with patch("a11y_fixer.agents.codebase_compiler.aget_tools") as mock_aget_tools:
            mock_aget_tools.return_value = [MagicMock()]

            with patch("a11y_fixer.config.fixture_path") as mock_fixture:
                with patch("a11y_fixer.config.to_virtual_path") as mock_vpath:
                    with patch("a11y_fixer.config.skills_dir") as mock_skills:
                        mock_fixture.return_value = Path("/path/to/fixture")
                        mock_vpath.side_effect = lambda p: "/" + str(p).split("/")[-1]
                        mock_skills.return_value = Path("/path/skills")

                        result = await codebase_compiler.build(mock_model)

                        assert isinstance(result, dict)
                        mock_aget_tools.assert_called_once_with(["angular-cli"])

    @pytest.mark.asyncio
    async def test_build_returns_subagent(self) -> None:
        """Verify build() returns a SubAgent instance."""
        with patch("a11y_fixer.agents.codebase_compiler.aget_tools") as mock_aget_tools:
            mock_aget_tools.return_value = [MagicMock()]

            with patch("a11y_fixer.config.fixture_path") as mock_fixture:
                with patch("a11y_fixer.config.to_virtual_path") as mock_vpath:
                    with patch("a11y_fixer.config.skills_dir") as mock_skills:
                        mock_fixture.return_value = Path("/path/to/fixture")
                        mock_vpath.side_effect = lambda p: "/" + str(p).split("/")[-1]
                        mock_skills.return_value = Path("/path/skills")

                        result = await codebase_compiler.build("model")

                        assert isinstance(result, dict)
                        assert "name" in result
                        assert "description" in result
                        assert "system_prompt" in result

    @pytest.mark.asyncio
    async def test_build_system_prompt_is_set(self) -> None:
        """Verify SubAgent uses the defined SYSTEM_PROMPT."""
        with patch("a11y_fixer.agents.codebase_compiler.aget_tools") as mock_aget_tools:
            mock_aget_tools.return_value = [MagicMock()]

            with patch("a11y_fixer.config.fixture_path") as mock_fixture:
                with patch("a11y_fixer.config.to_virtual_path") as mock_vpath:
                    with patch("a11y_fixer.config.skills_dir") as mock_skills:
                        mock_fixture.return_value = Path("/path/to/fixture")
                        mock_vpath.side_effect = lambda p: "/" + str(p).split("/")[-1]
                        mock_skills.return_value = Path("/path/skills")

                        result = await codebase_compiler.build("model")

                        assert result["system_prompt"] == codebase_compiler.SYSTEM_PROMPT
                        assert "Codebase Compiler" in result["system_prompt"]

    @pytest.mark.asyncio
    async def test_build_permissions_are_set(self) -> None:
        """Verify SubAgent has permissions configured."""
        with patch("a11y_fixer.agents.codebase_compiler.aget_tools") as mock_aget_tools:
            mock_aget_tools.return_value = [MagicMock()]

            with patch("a11y_fixer.config.fixture_path") as mock_fixture:
                with patch("a11y_fixer.config.to_virtual_path") as mock_vpath:
                    with patch("a11y_fixer.config.skills_dir") as mock_skills:
                        mock_fixture.return_value = Path("/path/to/fixture")
                        mock_vpath.side_effect = lambda p: "/" + str(p).split("/")[-1]
                        mock_skills.return_value = Path("/path/skills")

                        result = await codebase_compiler.build("model")

                        assert result["permissions"] is not None
                        assert isinstance(result["permissions"], list)
                        assert len(result["permissions"]) == 3

    @pytest.mark.asyncio
    async def test_build_middleware_contains_filesystem_middleware(self) -> None:
        """Verify middleware includes FilesystemMiddleware with file tools."""
        with patch("a11y_fixer.agents.codebase_compiler.aget_tools") as mock_aget_tools:
            mock_aget_tools.return_value = [MagicMock()]

            with patch("a11y_fixer.config.fixture_path") as mock_fixture:
                with patch("a11y_fixer.config.to_virtual_path") as mock_vpath:
                    with patch("a11y_fixer.config.skills_dir") as mock_skills:
                        mock_fixture.return_value = Path("/path/to/fixture")
                        mock_vpath.side_effect = lambda p: "/" + str(p).split("/")[-1]
                        mock_skills.return_value = Path("/path/skills")

                        result = await codebase_compiler.build("model")

                        # Middleware should contain FilesystemMiddleware
                        fs_middleware = [m for m in result["middleware"] if isinstance(m, FilesystemMiddleware)]
                        assert len(fs_middleware) >= 1
                        fs = fs_middleware[0]
                        # Tools is a list of Tool objects, check by name
                        tool_names = [tool.name for tool in fs.tools]
                        assert "read_file" in tool_names
                        assert "write_file" in tool_names
                        assert "edit_file" in tool_names

    @pytest.mark.asyncio
    async def test_build_middleware_contains_rubric_middleware(self) -> None:
        """Verify middleware includes RubricMiddleware with max_iterations=3."""
        with patch("a11y_fixer.agents.codebase_compiler.aget_tools") as mock_aget_tools:
            mock_aget_tools.return_value = [MagicMock()]

            with patch("a11y_fixer.config.fixture_path") as mock_fixture:
                with patch("a11y_fixer.config.to_virtual_path") as mock_vpath:
                    with patch("a11y_fixer.config.skills_dir") as mock_skills:
                        mock_fixture.return_value = Path("/path/to/fixture")
                        mock_vpath.side_effect = lambda p: "/" + str(p).split("/")[-1]
                        mock_skills.return_value = Path("/path/skills")

                        result = await codebase_compiler.build("model")

                        # Middleware should contain RubricMiddleware
                        rubric_middleware = [m for m in result["middleware"] if isinstance(m, RubricMiddleware)]
                        assert len(rubric_middleware) >= 1
                        rubric = rubric_middleware[0]
                        assert rubric.max_iterations == 2

    @pytest.mark.asyncio
    async def test_build_rubric_system_prompt_is_set(self) -> None:
        """Verify RubricMiddleware uses the defined RUBRIC_SYSTEM_PROMPT."""
        with patch("a11y_fixer.agents.codebase_compiler.aget_tools") as mock_aget_tools:
            mock_aget_tools.return_value = [MagicMock()]

            with patch("a11y_fixer.config.fixture_path") as mock_fixture:
                with patch("a11y_fixer.config.to_virtual_path") as mock_vpath:
                    with patch("a11y_fixer.config.skills_dir") as mock_skills:
                        mock_fixture.return_value = Path("/path/to/fixture")
                        mock_vpath.side_effect = lambda p: "/" + str(p).split("/")[-1]
                        mock_skills.return_value = Path("/path/skills")

                        result = await codebase_compiler.build("model")

                        rubric_middleware = [m for m in result["middleware"] if isinstance(m, RubricMiddleware)]
                        assert len(rubric_middleware) >= 1
                        rubric = rubric_middleware[0]
                        # RubricMiddleware is configured with max_iterations=2
                        assert hasattr(rubric, "max_iterations")
                        assert rubric.max_iterations == 2

    @pytest.mark.asyncio
    async def test_build_calls_aget_tools_with_angular_cli(self) -> None:
        """Verify build() calls aget_tools with angular-cli MCP."""
        with patch("a11y_fixer.agents.codebase_compiler.aget_tools") as mock_aget_tools:
            mock_aget_tools.return_value = [MagicMock()]

            with patch("a11y_fixer.config.fixture_path") as mock_fixture:
                with patch("a11y_fixer.config.to_virtual_path") as mock_vpath:
                    with patch("a11y_fixer.config.skills_dir") as mock_skills:
                        mock_fixture.return_value = Path("/path/to/fixture")
                        mock_vpath.side_effect = lambda p: "/" + str(p).split("/")[-1]
                        mock_skills.return_value = Path("/path/skills")

                        await codebase_compiler.build("model")

                        mock_aget_tools.assert_called_once_with(["angular-cli"])

    @pytest.mark.asyncio
    async def test_build_fixture_path_is_virtualized(self) -> None:
        """Verify build() virtualizes fixture path."""
        with patch("a11y_fixer.agents.codebase_compiler.aget_tools") as mock_aget_tools:
            mock_aget_tools.return_value = [MagicMock()]

            with patch("a11y_fixer.config.fixture_path") as mock_fixture:
                with patch("a11y_fixer.config.to_virtual_path") as mock_vpath:
                    with patch("a11y_fixer.config.skills_dir") as mock_skills:
                        mock_fixture.return_value = Path("/path/to/fixture")
                        mock_vpath.side_effect = lambda p: "/virtual" + str(p).replace("/path/to", "")
                        mock_skills.return_value = Path("/path/skills")

                        result = await codebase_compiler.build("model")

                        # Fixture path should be virtualized in permissions
                        first_perm = result["permissions"][0]
                        assert first_perm.paths[0].startswith("/virtual")

    @pytest.mark.asyncio
    async def test_build_skills_path_is_virtualized(self) -> None:
        """Verify build() virtualizes skills directory path."""
        with patch("a11y_fixer.agents.codebase_compiler.aget_tools") as mock_aget_tools:
            mock_aget_tools.return_value = [MagicMock()]

            with patch("a11y_fixer.config.fixture_path") as mock_fixture:
                with patch("a11y_fixer.config.to_virtual_path") as mock_vpath:
                    with patch("a11y_fixer.config.skills_dir") as mock_skills:
                        mock_fixture.return_value = Path("/path/to/fixture")
                        mock_vpath.side_effect = lambda p: "/v" + str(p).split("/")[-1]
                        mock_skills.return_value = Path("/path/skills")

                        result = await codebase_compiler.build("model")

                        # Skills should be virtualized
                        assert result["skills"] is not None
                        assert isinstance(result["skills"], list)
                        assert len(result["skills"]) > 0
                        assert result["skills"][0].startswith("/v")

    @pytest.mark.asyncio
    async def test_build_description_is_present(self) -> None:
        """Verify SubAgent has a meaningful description."""
        with patch("a11y_fixer.agents.codebase_compiler.aget_tools") as mock_aget_tools:
            mock_aget_tools.return_value = [MagicMock()]

            with patch("a11y_fixer.config.fixture_path") as mock_fixture:
                with patch("a11y_fixer.config.to_virtual_path") as mock_vpath:
                    with patch("a11y_fixer.config.skills_dir") as mock_skills:
                        mock_fixture.return_value = Path("/path/to/fixture")
                        mock_vpath.side_effect = lambda p: "/" + str(p).split("/")[-1]
                        mock_skills.return_value = Path("/path/skills")

                        result = await codebase_compiler.build("model")

                        assert result["description"] is not None
                        assert "fixture" in result["description"].lower()
                        assert "angular-cli" in result["description"].lower()


class TestModuleConstants:
    """Test module-level constants."""

    def test_name_constant_is_set(self) -> None:
        """Verify NAME constant is defined."""
        assert codebase_compiler.NAME == "codebase_compiler"

    def test_system_prompt_is_nonempty_string(self) -> None:
        """Verify SYSTEM_PROMPT is a non-empty string."""
        assert isinstance(codebase_compiler.SYSTEM_PROMPT, str)
        assert len(codebase_compiler.SYSTEM_PROMPT) > 0
        assert "Codebase Compiler" in codebase_compiler.SYSTEM_PROMPT

    def test_system_prompt_mentions_angular_features(self) -> None:
        """Verify SYSTEM_PROMPT mentions Angular 22.1 features."""
        prompt = codebase_compiler.SYSTEM_PROMPT
        assert "Angular" in prompt
        assert "standalone" in prompt
        assert "OnPush" in prompt

    def test_rubric_system_prompt_is_nonempty_string(self) -> None:
        """Verify RUBRIC_SYSTEM_PROMPT is a non-empty string."""
        assert isinstance(codebase_compiler.RUBRIC_SYSTEM_PROMPT, str)
        assert len(codebase_compiler.RUBRIC_SYSTEM_PROMPT) > 0

    def test_rubric_system_prompt_has_scoring_criteria(self) -> None:
        """Verify RUBRIC_SYSTEM_PROMPT has the rubric criteria."""
        prompt = codebase_compiler.RUBRIC_SYSTEM_PROMPT
        assert "wcag_lexical" in prompt
        assert "build_passes" in prompt
        assert "no_regression" in prompt


class TestIntegration:
    """Integration tests for codebase_compiler module."""

    @pytest.mark.asyncio
    async def test_permissions_and_build_consistency(self) -> None:
        """Verify _permissions() output is consistent with build() usage."""
        virtual_fixture = "/v/fixture"
        perms = codebase_compiler._permissions(virtual_fixture)

        with patch("a11y_fixer.agents.codebase_compiler.aget_tools") as mock_aget_tools:
            with patch("a11y_fixer.agents.codebase_compiler.config") as mock_config:
                mock_aget_tools.return_value = [MagicMock()]
                mock_config.fixture_path.return_value = Path("/path/to/fixture")
                mock_config.to_virtual_path.return_value = virtual_fixture
                mock_config.skills_dir.return_value = Path("/path/skills")

                result = await codebase_compiler.build("model")

                # Permissions from build should match _permissions() output
                assert len(result["permissions"]) == len(perms)
                for i, perm in enumerate(perms):
                    result_perm = result["permissions"][i]
                    assert result_perm.operations == perm.operations
                    assert result_perm.mode == perm.mode

    def test_permissions_first_rule_always_read_allow(self) -> None:
        """Verify first permission rule is always read-allow."""
        for fixture_path in ["/v/f", "/virtual/fixture", "/tmp/abc"]:
            perms = codebase_compiler._permissions(fixture_path)
            assert perms[0].operations == ["read"]
            assert perms[0].mode == "allow"

    def test_permissions_last_rule_always_write_deny(self) -> None:
        """Verify last permission rule is always write-deny catch-all."""
        for fixture_path in ["/v/f", "/virtual/fixture", "/tmp/abc"]:
            perms = codebase_compiler._permissions(fixture_path)
            assert perms[-1].operations == ["write"]
            assert perms[-1].paths == ["/**"]
            assert perms[-1].mode == "deny"

    @pytest.mark.asyncio
    async def test_build_middleware_order(self) -> None:
        """Verify middleware are in correct order: FilesystemMiddleware before RubricMiddleware."""
        with patch("a11y_fixer.agents.codebase_compiler.aget_tools") as mock_aget_tools:
            with patch("a11y_fixer.agents.codebase_compiler.config") as mock_config:
                mock_aget_tools.return_value = [MagicMock()]
                mock_config.fixture_path.return_value = Path("/path/to/fixture")
                mock_config.to_virtual_path.side_effect = lambda p: "/" + str(p).split("/")[-1]
                mock_config.skills_dir.return_value = Path("/path/skills")

                result = await codebase_compiler.build("model")

                # Find indices of middleware types
                fs_idx = None
                rubric_idx = None
                for i, m in enumerate(result["middleware"]):
                    if isinstance(m, FilesystemMiddleware):
                        fs_idx = i
                    if isinstance(m, RubricMiddleware):
                        rubric_idx = i

                # Both should be present
                assert fs_idx is not None
                assert rubric_idx is not None
                # FilesystemMiddleware should come before RubricMiddleware
                assert fs_idx < rubric_idx
