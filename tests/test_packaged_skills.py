"""Tests for packaged skill loading via load_skill()."""

from __future__ import annotations

import pytest

from pyflue import AgentProfile, define_agent_profile, load_skill


def test_load_skill_reads_skill_md(tmp_path):
    skill_dir = tmp_path / "review-checklist"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: review_checklist\ndescription: Review checklist\n---\nFollow the checklist.\n",
        encoding="utf-8",
    )

    skill = load_skill(skill_dir / "SKILL.md")
    assert skill.name == "review_checklist"
    assert skill.description == "Review checklist"
    assert "Follow the checklist." in skill.instructions

    # A loaded skill is usable directly as a packaged skill on a profile/agent.
    profile = define_agent_profile(AgentProfile(name="reviewer", skills=[skill]))
    assert profile.skills[0].name == "review_checklist"


def test_load_skill_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_skill(tmp_path / "nope.md")
