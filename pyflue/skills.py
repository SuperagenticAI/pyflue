"""Markdown skill discovery and parsing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import frontmatter

from pyflue.types import Role, Skill


def load_skills(root: str | Path = ".", skills_dir: str | Path | None = None) -> dict[str, Skill]:
    """Load Markdown skills from `.agents/skills` by default."""
    base = Path(root).expanduser().resolve()
    directory = Path(skills_dir).expanduser() if skills_dir else base / ".agents" / "skills"
    if not directory.is_absolute():
        directory = base / directory
    if not directory.exists():
        return {}

    skills: dict[str, Skill] = {}
    for path in sorted(directory.rglob("*.md")):
        skill = parse_skill(path)
        skills[skill.name] = skill
    return skills


def load_skill_by_path(
    root: str | Path = ".",
    rel_path: str = "",
    skills_dir: str | Path | None = None,
) -> Skill | None:
    """Load one skill by relative path under the skill directory."""
    base = Path(root).expanduser().resolve()
    directory = Path(skills_dir).expanduser() if skills_dir else base / ".agents" / "skills"
    if not directory.is_absolute():
        directory = base / directory
    target = (directory / rel_path).resolve()
    try:
        target.relative_to(directory.resolve())
    except ValueError:
        raise ValueError(f"Skill path escapes skills directory: {rel_path}") from None
    if not target.exists() or not target.is_file():
        return None
    return parse_skill(target)


def load_project_instructions(root: str | Path = ".") -> str:
    """Load root-level project instructions for the harness system prompt."""
    base = Path(root).expanduser().resolve()
    parts: list[str] = []
    for filename in ["AGENTS.md", "CLAUDE.md"]:
        path = base / filename
        if path.exists():
            parts.append(path.read_text(encoding="utf-8").strip())
    return "\n\n".join(part for part in parts if part)


def load_roles(root: str | Path = ".", roles_dir: str | Path | None = None) -> dict[str, Role]:
    """Load Markdown roles from `.agents/roles` by default."""
    base = Path(root).expanduser().resolve()
    directory = Path(roles_dir).expanduser() if roles_dir else base / ".agents" / "roles"
    if not directory.is_absolute():
        directory = base / directory
    if not directory.exists():
        return {}

    roles: dict[str, Role] = {}
    for path in sorted(directory.rglob("*.md")):
        role = parse_role(path)
        roles[role.name] = role
    return roles


def parse_skill(path: str | Path) -> Skill:
    """Parse one frontmatter Markdown skill file."""
    skill_path = Path(path).expanduser().resolve()
    default_name = skill_path.parent.name if skill_path.name.upper() == "SKILL.MD" else skill_path.stem
    return parse_skill_text(
        skill_path.read_text(encoding="utf-8"),
        default_name=default_name,
        path=skill_path,
    )


def parse_skill_text(
    content: str,
    *,
    default_name: str,
    path: str | Path | None = None,
) -> Skill:
    """Parse one frontmatter Markdown skill string."""
    post = frontmatter.loads(content)
    metadata: dict[str, Any] = dict(post.metadata or {})
    name = str(metadata.get("name") or default_name).strip()
    if not name:
        raise ValueError(f"Skill name cannot be empty: {path or default_name}")
    return Skill(
        name=name,
        description=str(metadata.get("description", "") or "").strip(),
        instructions=str(post.content or "").strip(),
        input_schema=_schema_or_none(metadata.get("input_schema")),
        output_schema=_schema_or_none(metadata.get("output_schema")),
        path=Path(path).expanduser().resolve() if path is not None else None,
    )


def parse_role(path: str | Path) -> Role:
    """Parse one frontmatter Markdown role file."""
    role_path = Path(path).expanduser().resolve()
    post = frontmatter.loads(role_path.read_text(encoding="utf-8"))
    metadata: dict[str, Any] = dict(post.metadata or {})
    name = str(metadata.get("name") or role_path.stem).strip()
    if not name:
        raise ValueError(f"Role name cannot be empty: {role_path}")
    return Role(
        name=name,
        description=str(metadata.get("description", "") or "").strip(),
        instructions=str(post.content or "").strip(),
        model=str(metadata["model"]).strip() if metadata.get("model") else None,
        thinking_level=str(metadata["thinking_level"]).strip() if metadata.get("thinking_level") else None,
        path=role_path,
    )


def render_skill_prompt(skill: Skill, args: dict[str, Any] | None = None) -> str:
    """Render a skill invocation prompt."""
    parts = [skill.instructions]
    if args:
        import json

        parts.append("Arguments:\n" + json.dumps(args, indent=2, sort_keys=True))
    if skill.output_schema:
        parts.append(
            "Return the final answer as JSON that satisfies this JSON schema:\n"
            + str(skill.output_schema)
        )
    return "\n\n".join(part for part in parts if part).strip()


def _schema_or_none(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None
