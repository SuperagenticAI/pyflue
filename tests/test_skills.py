from __future__ import annotations

from pyflue.skills import (
    load_project_instructions,
    load_roles,
    load_skill_by_path,
    load_skills,
    parse_skill,
    render_skill_prompt,
)


def test_parse_skill_frontmatter(tmp_path):
    path = tmp_path / "triage.md"
    path.write_text(
        """---
name: triage
description: Triage issues
input_schema:
  type: object
  properties:
    issue_number:
      type: integer
output_schema:
  type: object
  properties:
    summary:
      type: string
---
# Role
Do triage.
""",
        encoding="utf-8",
    )

    skill = parse_skill(path)

    assert skill.name == "triage"
    assert skill.description == "Triage issues"
    assert skill.input_schema["properties"]["issue_number"]["type"] == "integer"
    assert "Do triage" in skill.instructions


def test_load_skills_and_render_prompt(tmp_path):
    skills_dir = tmp_path / ".agents" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "one.md").write_text(
        "---\nname: one\n---\nDo one.",
        encoding="utf-8",
    )

    skills = load_skills(tmp_path)
    prompt = render_skill_prompt(skills["one"], args={"x": 1})

    assert list(skills) == ["one"]
    assert '"x": 1' in prompt


def test_load_deepagents_style_skill_directories(tmp_path):
    skills_dir = tmp_path / ".agents" / "skills"
    (skills_dir / "review").mkdir(parents=True)
    (skills_dir / "plan").mkdir(parents=True)
    (skills_dir / "review" / "SKILL.md").write_text("Review code.", encoding="utf-8")
    (skills_dir / "plan" / "SKILL.md").write_text("Make a plan.", encoding="utf-8")

    skills = load_skills(tmp_path)

    assert sorted(skills) == ["plan", "review"]
    assert skills["review"].instructions == "Review code."


def test_load_skill_by_relative_path(tmp_path):
    skills_dir = tmp_path / ".agents" / "skills" / "triage"
    skills_dir.mkdir(parents=True)
    (skills_dir / "reproduce.md").write_text(
        "---\nname: reproduce\n---\nReproduce the issue.",
        encoding="utf-8",
    )

    skill = load_skill_by_path(tmp_path, "triage/reproduce.md")

    assert skill is not None
    assert skill.name == "reproduce"
    assert skill.instructions == "Reproduce the issue."


def test_load_project_instructions(tmp_path):
    (tmp_path / "AGENTS.md").write_text("Base instructions", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("Extra instructions", encoding="utf-8")

    instructions = load_project_instructions(tmp_path)

    assert "Base instructions" in instructions
    assert "Extra instructions" in instructions


def test_load_role_model_frontmatter(tmp_path):
    role_dir = tmp_path / ".agents" / "roles"
    role_dir.mkdir(parents=True)
    (role_dir / "coder.md").write_text(
        "---\nname: coder\nmodel: role-model\n---\nReview code.",
        encoding="utf-8",
    )

    roles = load_roles(tmp_path)

    assert roles["coder"].model == "role-model"
