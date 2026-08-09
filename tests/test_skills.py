"""Tests for KiCad design skills, hooks and agents file integrity."""

import json
import os
import re
import stat
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SKILLS_DIR = REPO_ROOT / "skills"
HOOKS_DIR = REPO_ROOT / "hooks"
AGENTS_DIR = REPO_ROOT / "agents"

EXPECTED_SKILLS = [
    "circuit-design",
    "schematic-plan",
    "schematic-design",
    "pcb-layout",
    "verification",
    "using-kicad",
]

EXPECTED_AGENTS = [
    "bom-reviewer.md",
    "schematic-plan-reviewer.md",
    "code-reviewer.md",
]


class TestSkillFileIntegrity:
    """Verify all expected skill files exist and have valid structure."""

    @pytest.mark.parametrize("skill_name", EXPECTED_SKILLS)
    def test_skill_directory_exists(self, skill_name: str) -> None:
        skill_dir = SKILLS_DIR / skill_name
        assert skill_dir.is_dir(), f"Skill directory missing: {skill_dir}"

    @pytest.mark.parametrize("skill_name", EXPECTED_SKILLS)
    def test_skill_file_exists(self, skill_name: str) -> None:
        skill_file = SKILLS_DIR / skill_name / "SKILL.md"
        assert skill_file.is_file(), f"SKILL.md missing: {skill_file}"

    @pytest.mark.parametrize("skill_name", EXPECTED_SKILLS)
    def test_skill_has_frontmatter(self, skill_name: str) -> None:
        skill_file = SKILLS_DIR / skill_name / "SKILL.md"
        content = skill_file.read_text()
        assert content.startswith("---"), f"{skill_name} missing frontmatter start"
        # Find second --- delimiter
        second_delimiter = content.index("---", 3)
        assert second_delimiter > 3, f"{skill_name} missing frontmatter end"
        frontmatter = content[3:second_delimiter].strip()
        assert "name:" in frontmatter, f"{skill_name} frontmatter missing 'name:'"
        assert "description:" in frontmatter, f"{skill_name} frontmatter missing 'description:'"

    @pytest.mark.parametrize("skill_name", EXPECTED_SKILLS)
    def test_skill_name_matches_directory(self, skill_name: str) -> None:
        skill_file = SKILLS_DIR / skill_name / "SKILL.md"
        content = skill_file.read_text()
        second_delimiter = content.index("---", 3)
        frontmatter = content[3:second_delimiter]
        # Extract name from frontmatter
        for line in frontmatter.split("\n"):
            if line.strip().startswith("name:"):
                name_value = line.split(":", 1)[1].strip()
                assert name_value == skill_name, (
                    f"Skill name '{name_value}' doesn't match directory '{skill_name}'"
                )
                break

    @pytest.mark.parametrize("skill_name", EXPECTED_SKILLS)
    def test_skill_has_critical_rule(self, skill_name: str) -> None:
        content = (SKILLS_DIR / skill_name / "SKILL.md").read_text()
        assert "<CRITICAL-RULE>" in content, f"{skill_name} missing CRITICAL-RULE"
        assert "</CRITICAL-RULE>" in content, f"{skill_name} missing CRITICAL-RULE closing tag"

    @pytest.mark.parametrize("skill_name", EXPECTED_SKILLS)
    def test_critical_rule_forbids_kicad_file_editing(self, skill_name: str) -> None:
        """Every skill's CRITICAL-RULE must forbid Read/Write/Edit on KiCad files."""
        content = (SKILLS_DIR / skill_name / "SKILL.md").read_text()
        start = content.index("<CRITICAL-RULE>")
        end = content.index("</CRITICAL-RULE>")
        critical_rule = content[start:end]
        assert ".kicad_sch" in critical_rule, (
            f"{skill_name} CRITICAL-RULE doesn't mention .kicad_sch"
        )
        assert "MCP tools" in critical_rule or "MCP tool" in critical_rule, (
            f"{skill_name} CRITICAL-RULE doesn't reference MCP tools"
        )


class TestHooksIntegrity:
    """Verify hook system is correctly configured."""

    def test_hooks_json_exists(self) -> None:
        hooks_file = HOOKS_DIR / "hooks.json"
        assert hooks_file.is_file(), "hooks/hooks.json missing"

    def test_hooks_json_valid(self) -> None:
        hooks_file = HOOKS_DIR / "hooks.json"
        data = json.loads(hooks_file.read_text())
        assert "hooks" in data, "hooks.json missing 'hooks' key"

    def test_session_start_hook_defined(self) -> None:
        hooks_file = HOOKS_DIR / "hooks.json"
        data = json.loads(hooks_file.read_text())
        assert "SessionStart" in data["hooks"], "SessionStart hook missing"
        session_hooks = data["hooks"]["SessionStart"]
        assert len(session_hooks) > 0, "SessionStart has no hook entries"

    def test_session_start_hook_runs_script(self) -> None:
        """SessionStart hook must execute the session-start script."""
        hooks_file = HOOKS_DIR / "hooks.json"
        data = json.loads(hooks_file.read_text())
        session_hooks = data["hooks"]["SessionStart"]
        # At least one hook entry must reference session-start
        commands = [h.get("command", "") for entry in session_hooks for h in entry.get("hooks", [])]
        assert any("session-start" in cmd for cmd in commands), (
            "No SessionStart hook references session-start script"
        )

    def test_pre_tool_use_hook_defined(self) -> None:
        hooks_file = HOOKS_DIR / "hooks.json"
        data = json.loads(hooks_file.read_text())
        assert "PreToolUse" in data["hooks"], "PreToolUse hook missing"

    def test_pre_tool_use_blocks_kicad_files(self) -> None:
        """PreToolUse hook must block Read/Write/Edit on KiCad files."""
        hooks_file = HOOKS_DIR / "hooks.json"
        data = json.loads(hooks_file.read_text())
        pre_tool_hooks = data["hooks"]["PreToolUse"]
        # Serialize to check for KiCad file extensions
        serialized = json.dumps(pre_tool_hooks)
        assert ".kicad_sch" in serialized, "PreToolUse hook doesn't check for .kicad_sch files"
        assert "block" in serialized, "PreToolUse hook doesn't block access"

    def test_session_start_script_exists(self) -> None:
        script = HOOKS_DIR / "session-start"
        assert script.is_file(), "hooks/session-start script missing"

    @pytest.mark.skipif(
        os.name == "nt",
        reason="Execute permission check not applicable on Windows",
    )
    def test_session_start_script_executable(self) -> None:
        script = HOOKS_DIR / "session-start"
        mode = script.stat().st_mode
        assert mode & stat.S_IXUSR, "hooks/session-start not executable"

    def test_session_start_references_using_kicad(self) -> None:
        script = HOOKS_DIR / "session-start"
        content = script.read_text()
        assert "using-kicad" in content, "session-start doesn't reference using-kicad skill"

    def test_session_start_outputs_json(self) -> None:
        """session-start script must output JSON for hook system."""
        script = HOOKS_DIR / "session-start"
        content = script.read_text()
        assert "hookSpecificOutput" in content or "additional_context" in content, (
            "session-start doesn't output expected JSON structure"
        )

    def test_plugin_json_no_inline_hooks(self) -> None:
        plugin_json = REPO_ROOT / ".claude-plugin" / "plugin.json"
        data = json.loads(plugin_json.read_text())
        assert "hooks" not in data, (
            "plugin.json still has inline hooks -- they should be in hooks/hooks.json"
        )


class TestVersionConsistency:
    """plugin.json is the single source of truth for the plugin version (issue #1)."""

    def test_plugin_json_matches_pyproject(self) -> None:
        plugin = json.loads((REPO_ROOT / ".claude-plugin" / "plugin.json").read_text())
        pyproject = (REPO_ROOT / "pyproject.toml").read_text()
        match = re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE)
        assert match, "pyproject.toml missing version"
        assert plugin["version"] == match.group(1), (
            f"plugin.json {plugin['version']} != pyproject.toml {match.group(1)}; "
            "release.yml bumps both together, don't bump one by hand"
        )

    def test_marketplace_entries_carry_no_version(self) -> None:
        marketplace_file = REPO_ROOT / ".claude-plugin" / "marketplace.json"
        for entry in json.loads(marketplace_file.read_text())["plugins"]:
            assert "version" not in entry, (
                f"marketplace entry '{entry['name']}' must not pin a version: Claude Code "
                "silently uses plugin.json's version instead, and release.yml only bumps "
                "plugin.json"
            )

    def test_marketplace_source_is_relative(self) -> None:
        marketplace_file = REPO_ROOT / ".claude-plugin" / "marketplace.json"
        plugins = json.loads(marketplace_file.read_text())["plugins"]
        entry = next(e for e in plugins if e["name"] == "kicad")
        assert entry["source"] == "./", (
            "the kicad plugin lives in this repo, so its marketplace source must be the "
            "relative './'; a machine-local or SSH source breaks `claude plugin install`"
        )


class TestAgentsIntegrity:
    """Verify agent prompt files exist and have required structure."""

    @pytest.mark.parametrize("agent_file", EXPECTED_AGENTS)
    def test_agent_file_exists(self, agent_file: str) -> None:
        agent_path = AGENTS_DIR / agent_file
        assert agent_path.is_file(), f"Agent file missing: {agent_path}"

    @pytest.mark.parametrize("agent_file", EXPECTED_AGENTS)
    def test_agent_has_content(self, agent_file: str) -> None:
        agent_path = AGENTS_DIR / agent_file
        content = agent_path.read_text()
        assert len(content) > 50, f"Agent file too short: {agent_file}"

    @pytest.mark.parametrize("agent_file", EXPECTED_AGENTS)
    def test_agent_has_role_definition(self, agent_file: str) -> None:
        """Each agent file should define its role/purpose."""
        agent_path = AGENTS_DIR / agent_file
        content = agent_path.read_text().lower()
        # Agent files should mention what they review or their purpose
        assert "review" in content or "role" in content or "you are" in content, (
            f"Agent file {agent_file} missing role definition"
        )
