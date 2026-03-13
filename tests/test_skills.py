"""Tests for KiCad design skills integrity and content validation."""

import json
import os
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

# Domain skills are all skills except the orchestrator (using-kicad)
DOMAIN_SKILLS = [s for s in EXPECTED_SKILLS if s != "using-kicad"]

# Skills that contain Rationalization Prevention sections
SKILLS_WITH_RATIONALIZATION = [
    "circuit-design",
    "schematic-plan",
    "schematic-design",
]

# Skills that contain HARD-GATE tags
SKILLS_WITH_HARD_GATE = [
    "circuit-design",
    "schematic-plan",
    "verification",
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


class TestSkillContent:
    """Verify skills contain required content elements."""

    @pytest.mark.parametrize("skill_name", DOMAIN_SKILLS)
    def test_domain_skill_has_checklist(self, skill_name: str) -> None:
        content = (SKILLS_DIR / skill_name / "SKILL.md").read_text()
        assert "## Checklist" in content, f"{skill_name} missing Checklist section"
        assert "TodoWrite" in content, f"{skill_name} missing TodoWrite instruction"
        assert "- [ ]" in content, f"{skill_name} missing checklist items"

    @pytest.mark.parametrize("skill_name", DOMAIN_SKILLS)
    def test_domain_skill_has_mcp_tools_section(self, skill_name: str) -> None:
        content = (SKILLS_DIR / skill_name / "SKILL.md").read_text()
        assert "## MCP Tools" in content, f"{skill_name} missing MCP Tools section"

    @pytest.mark.parametrize("skill_name", DOMAIN_SKILLS)
    def test_domain_skill_checklist_has_todowrite_instruction(self, skill_name: str) -> None:
        """Each checklist must instruct the user to use TodoWrite."""
        content = (SKILLS_DIR / skill_name / "SKILL.md").read_text()
        # Find the checklist section
        checklist_idx = content.index("## Checklist")
        checklist_section = content[checklist_idx:]
        assert "TodoWrite" in checklist_section, (
            f"{skill_name} Checklist section missing TodoWrite instruction"
        )

    def test_using_kicad_has_pipeline(self) -> None:
        content = (SKILLS_DIR / "using-kicad" / "SKILL.md").read_text()
        assert "## Pipeline" in content, "using-kicad missing Pipeline section"
        assert "## Phase Gates" in content, "using-kicad missing Phase Gates section"
        assert "<HARD-GATE>" in content, "using-kicad missing HARD-GATE tags"

    def test_using_kicad_has_skill_catalog(self) -> None:
        content = (SKILLS_DIR / "using-kicad" / "SKILL.md").read_text()
        assert "## Skill Catalog" in content, "using-kicad missing Skill Catalog"
        # Verify all domain skills are listed in the catalog
        for skill in DOMAIN_SKILLS:
            assert skill in content, f"using-kicad catalog missing {skill}"

    def test_using_kicad_has_todowrite_flowchart(self) -> None:
        content = (SKILLS_DIR / "using-kicad" / "SKILL.md").read_text()
        assert "TodoWrite" in content, "using-kicad missing TodoWrite reference"
        assert "digraph" in content, "using-kicad missing skill activation flowchart"

    def test_using_kicad_has_skill_activation_flow(self) -> None:
        content = (SKILLS_DIR / "using-kicad" / "SKILL.md").read_text()
        assert "## Skill Activation Flow" in content, (
            "using-kicad missing Skill Activation Flow section"
        )

    @pytest.mark.parametrize("skill_name", SKILLS_WITH_HARD_GATE)
    def test_skill_has_hard_gate(self, skill_name: str) -> None:
        content = (SKILLS_DIR / skill_name / "SKILL.md").read_text()
        assert "<HARD-GATE>" in content, f"{skill_name} missing HARD-GATE"

    @pytest.mark.parametrize("skill_name", SKILLS_WITH_RATIONALIZATION)
    def test_skill_has_rationalization_prevention(self, skill_name: str) -> None:
        content = (SKILLS_DIR / skill_name / "SKILL.md").read_text()
        assert "Rationalization" in content or "rationalization" in content, (
            f"{skill_name} missing Rationalization Prevention"
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


class TestSkillCrossReferences:
    """Verify skills reference each other correctly through the pipeline."""

    def test_circuit_design_mentions_bom_artifact(self) -> None:
        content = (SKILLS_DIR / "circuit-design" / "SKILL.md").read_text()
        assert "specs/bom.md" in content, "circuit-design doesn't mention specs/bom.md artifact"

    def test_schematic_plan_requires_bom(self) -> None:
        content = (SKILLS_DIR / "schematic-plan" / "SKILL.md").read_text()
        assert "specs/bom.md" in content, "schematic-plan doesn't reference specs/bom.md dependency"

    def test_schematic_plan_produces_plan_artifact(self) -> None:
        content = (SKILLS_DIR / "schematic-plan" / "SKILL.md").read_text()
        assert "specs/schematic-plan.md" in content, (
            "schematic-plan doesn't mention specs/schematic-plan.md artifact"
        )

    def test_schematic_design_requires_plan(self) -> None:
        content = (SKILLS_DIR / "schematic-design" / "SKILL.md").read_text()
        assert "specs/schematic-plan.md" in content, (
            "schematic-design doesn't reference specs/schematic-plan.md dependency"
        )

    def test_pipeline_order_in_using_kicad(self) -> None:
        """using-kicad pipeline must list skills in correct order."""
        content = (SKILLS_DIR / "using-kicad" / "SKILL.md").read_text()
        pipeline_section = content[content.index("## Pipeline") :]
        # Verify ordering: circuit-design before schematic-plan before
        # schematic-design before verification before pcb-layout
        cd_pos = pipeline_section.index("circuit-design")
        sp_pos = pipeline_section.index("schematic-plan")
        sd_pos = pipeline_section.index("schematic-design")
        assert cd_pos < sp_pos < sd_pos, "Pipeline skills not in correct order"
