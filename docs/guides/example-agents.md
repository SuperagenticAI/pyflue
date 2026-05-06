# Example Agents

This page shows product-oriented agent categories in Python:

- issue triage
- data analyst
- coding agent
- support agent

The examples live under:

```text
examples/agents/
```

A model-free server/client smoke demo lives under:

```text
examples/server_client/
```

## Coding Agent

This example creates an agent with shell access, prepares a repository
workspace, and sends a coding prompt to the session.

```python
from pydantic import BaseModel
from pyflue import init


class CodingResult(BaseModel):
    summary: str
    files_changed: list[str] = []
    tests_run: list[str] = []


async def run_coding_agent(repo: str, prompt: str) -> CodingResult:
    agent = await init(
        model="openai:gpt-5.5",
        sandbox="virtual",
        allow_write=True,
        allow_shell=True,
        allowed_commands=["git", "mkdir", "python"],
    )
    session = await agent.session("code")

    await session.shell("mkdir -p workspace")
    await session.shell(f"git clone {repo} workspace/project")
    await session.shell("python -m pip install -e .", timeout=600)

    return await session.prompt(prompt, result=CodingResult)
```

Full file:

```text
examples/agents/coding_agent.py
```

## Issue Triage

The issue triage agent accepts issue data, uses a Markdown skill, and returns a
typed Pydantic result.

```python
from pydantic import BaseModel
from pyflue import init


class TriageResult(BaseModel):
    severity: str
    summary: str
    suggested_labels: list[str] = []
    fix_recommended: bool = False


async def triage_issue(issue_number: int, title: str, body: str) -> TriageResult:
    agent = await init(model="openai:gpt-5.5")
    session = await agent.session(f"issue-{issue_number}")
    return await session.skill(
        "triage_issue",
        args={"issue_number": issue_number, "title": title, "body": body},
        result=TriageResult,
    )
```

Full file:

```text
examples/agents/issue_triage.py
```

## Data Analyst

The data analyst agent writes data into the sandbox, uses shell tools to inspect
it, and asks the model for structured analysis.

```python
from pydantic import BaseModel
from pyflue import init


class AnalysisResult(BaseModel):
    summary: str
    metrics: dict[str, float]
    recommendations: list[str]


async def analyze_csv(csv_text: str, question: str) -> AnalysisResult:
    agent = await init(
        model="openai:gpt-5.5",
        python_backend="monty",
        allow_write=True,
    )
    session = await agent.session("data-analysis")
    await session.write_file("data/input.csv", csv_text)
    preview = await session.run_python(
        "len(rows)",
        inputs={"rows": csv_text.splitlines()},
    )
    return await session.prompt(
        f"Analyze data/input.csv for this question: {question}\n\nRows: {preview.result}",
        result=AnalysisResult,
    )
```

Full file:

```text
examples/agents/data_analyst.py
```

## Support Agent

The support agent writes knowledge base articles into the sandbox, searches
them with `grep`, and returns a typed answer.

```python
from pydantic import BaseModel
from pyflue import init


class SupportAnswer(BaseModel):
    answer: str
    cited_articles: list[str]
    confidence: str


async def answer_support_question(question: str, articles: dict[str, str]) -> SupportAnswer:
    agent = await init(model="openai:gpt-5.5", allow_write=True, allow_shell=True)
    session = await agent.session("support")

    for name, content in articles.items():
        await session.write_file(f"knowledge/{name}.md", content)

    matches = await session.shell(f"grep -Rni {question.split()[0]!r} knowledge || true")
    return await session.prompt(
        f"Answer this customer question using knowledge/*.md.\n\nQuestion: {question}\n\nSearch matches:\n{matches['stdout']}",
        result=SupportAnswer,
    )
```

Full file:

```text
examples/agents/support_agent.py
```

## Running Examples

Run the server/client smoke demo without a model key:

```bash
uv run python examples/server_client/run_smoke.py
```

Set a provider key for model-backed examples:

```bash
export OPENAI_API_KEY=...
python examples/agents/coding_agent.py
```

The examples are meant to be copied into your own project and adjusted for your
model provider, sandbox policy, and deployment target.
