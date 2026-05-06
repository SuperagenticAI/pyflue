<section class="hero">
  <h1>PyFlue</h1>
  <p>
    PyFlue is a Python-first agent harness framework for building autonomous
    agents with Markdown skills, persistent sessions, sandboxed filesystem
    access, shell execution, typed Pydantic outputs, and pluggable harness
    backends. It adapts the agent harness pattern for Python teams.
  </p>
</section>

```bash
uv add pyflue
pyflue init my-agent
cd my-agent
pyflue run --prompt "Review this project"
```

```bash
pip install pyflue
pyflue init my-agent
cd my-agent
pyflue run --prompt "Review this project"
```

## Why PyFlue

<div class="admonition warning">
  <p><strong>Warning:</strong> PyFlue is under active development. The API may change.
  Pin your dependencies and review changelogs before updating.</p>
</div>

PyFlue gives Python developers a framework-shaped agent runtime instead of a
collection of low-level primitives. The default backend is DeepAgents, with a
stable PyFlue API layered above it.

PyFlue provides Markdown skills, stateful sessions, sandboxed tools, typed
outputs, and deployable agent entrypoints for Python teams with Pydantic,
Python packaging, and Python-friendly deployment targets.

The public model is simple:

```python
agent = await init(model="openai:gpt-5.5", harness="deepagents")
session = await agent.session("issue-123")
result = await session.skill("triage", args={"issue_number": 123}, result=FixResult)
```

PyFlue is designed for Python teams that want the ergonomics of a modern agent
harness while keeping access to the Python ecosystem. It is useful for coding
agents, data workflows, support automation, and service agents that need
structured outputs and controlled access to files or shell commands.

<div class="feature-grid">
  <div class="feature-card">
    <h3>Markdown Skills</h3>
    <p>Define reusable workflows in <code>.agents/skills/*.md</code> with YAML frontmatter.</p>
  </div>
  <div class="feature-card">
    <h3>Stateful Sessions</h3>
    <p>Persist conversation history with SQLite-backed sessions.</p>
  </div>
  <div class="feature-card">
    <h3>Virtual Sandbox</h3>
    <p>Read, write, edit, grep, glob, and run shell commands behind policy gates.</p>
  </div>
  <div class="feature-card">
    <h3>Typed Outputs</h3>
    <p>Validate final results with Pydantic v2.</p>
  </div>
</div>

## What You Can Build

PyFlue gives you the core pieces needed for agentic workflows:

- project instructions from `AGENTS.md` and `CLAUDE.md`
- reusable Markdown skills in `.agents/skills`
- stateful sessions backed by SQLite
- a virtual sandbox with read, write, edit, grep, glob, and shell tools
- Pydantic validation for typed results
- cancellation for active prompt, stream, task, and shell operations
- structured command tools with `PyFlueCommand`
- a Python client for deployed PyFlue servers
- a DeepAgents runtime backend
- a backend registry for future OpenAI Agents, Google ADK, Pydantic AI, and
  custom harness backends
- streaming events through Python, CLI, and SSE
- route triggers for file-based webhook agents
- secret grants for shell and prompt calls
- command allowlists and compound-command protection
- deployment files for Docker/FastAPI, GitHub Actions, GitLab CI, Railway,
  Render, Fly.io, Vercel, Netlify, and Cloudflare Containers starter projects

## Minimal Python Example

```python
from pydantic import BaseModel
from pyflue import init


class FixResult(BaseModel):
    fix_applied: bool
    summary: str


async def main():
    agent = await init(
        model="openai:gpt-5.5",
        harness="deepagents",
        sandbox="virtual",
        allow_write=True,
        allow_shell=True,
    )

    session = await agent.session("fix-123")
    result = await session.skill(
        "triage",
        args={"issue_number": 123},
        result=FixResult,
    )

    if result.fix_applied:
        await session.shell("git status --short")
```

## Next Steps

- Start with [Getting Started](getting-started.md).
- Learn the [agent harness model](concepts/harness.md).
- Choose a [deployment target](deployment.md).
- Create your first [Markdown skill](guides/create-a-skill.md).
- Review the [feature matrix](reference/feature-matrix.md).
