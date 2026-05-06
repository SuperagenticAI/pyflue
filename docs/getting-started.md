# Getting Started

This guide creates a new PyFlue project, runs a prompt, and explains the files
that are generated.

## Requirements

- Python 3.11 or newer
- `uv` or `pip`
- A model provider key if you run a real model

PyFlue depends on DeepAgents `0.5.6`, which requires Python 3.11 or newer.

## Install

Add PyFlue to a project with `uv`:

```bash
uv add pyflue
```

Or install it with `pip`:

```bash
pip install pyflue
```

Optional extras:

```bash
uv add "pyflue[monty]"
uv add "pyflue[sandboxes]"
```

```bash
pip install "pyflue[monty]"
pip install "pyflue[sandboxes]"
```

## Create a Project

```bash
pyflue init my-agent
cd my-agent
```

To verify the server and client path without a model key, run the bundled smoke
demo from the PyFlue repository root:

```bash
uv run python examples/server_client/run_smoke.py
```

The command creates:

```text
AGENTS.md
pyflue.toml
.agents/
  roles/
    coder.md
  skills/
    triage.md
agents/
  default.py
```

## Run a Prompt

```bash
pyflue run --prompt "Review this project"
```

By default, PyFlue uses:

- `harness = "deepagents"`
- `sandbox = "virtual"`
- session id `default`
- read-only sandbox policy

Enable writes and shell execution explicitly:

```bash
pyflue run \
  --prompt "Inspect the project and write a short report to report.txt" \
  --allow-write \
  --allow-shell
```

## Use a Named Session

```bash
pyflue run --session issue-123 --prompt "Inspect the failure"
pyflue run --session issue-123 --prompt "Suggest the smallest fix"
```

Session history is stored under `.pyflue/sessions`.

## Project Configuration

`pyflue.toml` controls the default agent settings:

```toml
[agent]
model = "openai:gpt-5.5"
harness = "deepagents"
sandbox = "virtual"
skills_dir = ".agents/skills"
state_dir = ".pyflue/sessions"
allowed_commands = ["git", "pytest"]
typed_retries = 3
```

## Useful Commands

| Command | Purpose |
| --- | --- |
| `pyflue init` | Create a PyFlue project. |
| `pyflue run` | Run one prompt. |
| `pyflue run --stream` | Print normalized stream events. |
| `pyflue skill new` | Create a Markdown skill. |
| `pyflue dev` | Start the local webhook server and dashboard. |
| `pyflue build` | Generate deployment files. |
| `pyflue deploy` | Generate files and run a supported provider CLI when available. |

## Run The Dev Server

```bash
pyflue dev --port 2024
```

Open the dashboard:

```text
http://127.0.0.1:2024/__pyflue
```

Call the default file-based agent:

```bash
curl http://127.0.0.1:2024/agents/default/demo \
  -H "Content-Type: application/json" \
  -d '{"payload": {"prompt": "Review this repository"}}'
```
