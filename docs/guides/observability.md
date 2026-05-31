# Observability

PyFlue emits a correlated event stream for the work an agent or workflow
performs, and ships an OpenTelemetry adapter that turns that stream into spans.

## The event stream

Every session operation produces a bounded set of events. Each event carries
correlation fields (`operation_id`, `instance_id`, and, inside a workflow,
`run_id`) so a consumer can reconstruct the structure of the work.

| Event pair | Emitted for |
| --- | --- |
| `run_start` / `run_end` | A workflow run. |
| `operation_start` / `operation` | One session operation (prompt, skill, task, shell, or compact). |
| `turn_request` / `turn` | One model generation, including model id and token usage. |
| `tool_start` / `tool_call`, `command_start` / `command_end` | Tool and shell execution. |
| `task_start` / `task_end` | Delegated subagent work. |
| `compaction_start` / `compaction_end` | Context compaction. |
| `log` | Structured application logs from `ctx.log`. |

You can receive these events two ways:

- Per agent, by passing `on_event` to `init(...)` or a created agent.
- Application wide, by registering a callback through `observe(...)` for
  workflow run events.

## OpenTelemetry

Install the extra and register the adapter as an event observer.

```bash
pip install 'pyflue[otel]'
```

```python
from pyflue import init
from pyflue.observability import create_opentelemetry_observer

agent = await init(on_event=create_opentelemetry_observer())
```

The adapter builds this span tree, parenting by the correlation fields:

```text
flue.workflow <name>             run_start / run_end
  flue.operation <kind>          operation_start / operation
    gen_ai.generate              turn_request / turn  (model, token usage, cost)
    flue.tool <name>             command_* / tool_*
    flue.task                    task_start / task_end
    flue.compaction              compaction_start / compaction_end
```

Generation spans use the `gen_ai.*` semantic conventions: `gen_ai.request.model`,
`gen_ai.response.model`, and `gen_ai.usage.input_tokens`,
`gen_ai.usage.output_tokens`, `gen_ai.usage.total_tokens`, and
`gen_ai.usage.cost_total`.

By default the adapter exports correlation, timing, model, and usage metadata.
Set `capture_content=True` to also attach commands, results, and log messages.
That content can include sensitive data, so enable it deliberately.

```python
observer = create_opentelemetry_observer(capture_content=True)
```

## Provide your own tracer

Pass a configured tracer when your application already sets up an OpenTelemetry
SDK and exporter.

```python
from opentelemetry import trace
from pyflue.observability import create_opentelemetry_observer

observer = create_opentelemetry_observer(tracer=trace.get_tracer("my-app"))
```

## Scope and limits

PyFlue emits one generation span per backend operation, carrying that
operation's model and aggregate token usage. A span for each internal agent
loop turn depends on backend level instrumentation and is tracked as future
work. See [Parity with Flue](../reference/flue-parity.md) for the full status.
