# Models

Models determine what kind of work an agent can do. Providers determine how
PyFlue reaches those models, authenticates requests, and applies endpoint
configuration.

PyFlue keeps model selection explicit and backend-friendly. The framework passes
the selected model string and provider settings to the active harness backend
rather than owning a full global model catalog.

## Model Strings

Use a provider-qualified model string when the backend supports one:

| Model string | Typical provider |
| --- | --- |
| `anthropic/claude-sonnet-4-6` | Anthropic |
| `openai/gpt-5.5` | OpenAI |
| `openrouter/moonshotai/kimi-k2.6` | OpenRouter |
| `ollama:qwen3:8b` | Local Ollama through Pydantic AI |

```python title="src/agents/reviewer.py"
from pyflue import create_agent

default = create_agent(
    lambda ctx: {
        "model": "anthropic/claude-sonnet-4-6",
        "instructions": "Review the requested change and report evidence.",
    }
)
```

The model can also come from `pyflue.toml`, a reusable profile, a subagent
profile, or a per-call override.

```python
result = await session.prompt(
    "Use a larger model for this review.",
    model="openai/gpt-5.5",
)
```

## Thinking Level

Reasoning effort is configured with `thinking_level`. Supported values are
`off`, `minimal`, `low`, `medium`, `high`, and `xhigh`.

```python
default = create_agent(
    lambda ctx: {
        "model": "anthropic/claude-sonnet-4-6",
        "thinking_level": "high",
    }
)
```

`thinking_level` can be set on an agent, profile, role, prompt, skill, or task.
Support depends on the selected backend, provider, and model. Unsupported paths
may ignore the hint.

## Authentication

Most hosted providers read credentials from standard environment variables used
by the selected backend:

| Provider | Common environment variable |
| --- | --- |
| Anthropic | `ANTHROPIC_API_KEY` |
| OpenAI | `OPENAI_API_KEY` |
| OpenRouter | `OPENROUTER_API_KEY` |

Keep credentials out of agent modules and committed config. For local
development, load them through your shell, `.env` tooling, or the `pyflue dev
--env` option.

## Provider Overrides

Use provider settings for gateways, enterprise endpoints, custom headers, or
OpenAI Responses-compatible hosted response storage.

```python
from pyflue import init

agent = await init(
    model="openai/gpt-5.5",
    providers={
        "openai": {
            "base_url": "https://litellm.example.com/openai",
            "api_key": "gateway-key",
            "headers": {"X-Team": "support-agents"},
            "store_responses": True,
        }
    },
)
```

Equivalent TOML:

```toml
[agent]
model = "openai/gpt-5.5"

[providers.openai]
base_url = "https://litellm.example.com/openai"
api_key = "gateway-key"
store_responses = true

[providers.openai.headers]
X-Team = "support-agents"
```

Provider overrides are passed to the harness backend. They do not register a
global provider catalog inside PyFlue.

## Backend Behavior

The default Pydantic AI backend accepts `provider/model` and `provider:model`
forms and normalizes them for Pydantic AI. It can also use local providers such
as Ollama when the required Python packages and services are available.

```python
agent = await init(harness="pydanticai", model="ollama:qwen3:8b")
```

DeepAgents and custom harnesses may have their own supported model strings and
credential conventions. See [Harness Backends](../api/harness-backends.md) when
you need backend-specific details.

## Portability Notes

TypeScript Flue owns a deeper provider-routing layer and model catalog. PyFlue
intentionally keeps that layer thinner today so Python harness backends can
remain the source of truth for provider support.

For portable projects:

- use provider-qualified model strings
- keep provider credentials in environment variables
- document which harness backend the project expects
- keep endpoint overrides in `pyflue.toml` or application startup code
- test model strings in the same backend used in production
