"""DeepAgents harness backend."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from pyflue.harnesses.base import HarnessBackend
from pyflue.sandboxes.base import SandboxBackend
from pyflue.types import (
    HarnessResult,
    PromptCost,
    PromptModel,
    PromptUsage,
    PyFlueConfig,
    PyFlueEvent,
    Skill,
)

try:
    from deepagents.backends.protocol import (
        SandboxBackendProtocol as _DeepAgentsSandboxBase,
    )
except Exception:
    _DeepAgentsSandboxBase = object


class DeepAgentsBackend(HarnessBackend):
    """Default PyFlue harness powered by the public DeepAgents API."""

    name = "deepagents"

    async def run(
        self,
        *,
        prompt: str,
        system_prompt: str,
        config: PyFlueConfig,
        skills: dict[str, Skill],
        sandbox: Any,
        session_id: str,
        python_backend: Any | None = None,
        tools: list[Any] | tuple[Any, ...] | None = None,
        images: list[Any] | tuple[Any, ...] | None = None,
        stream: bool = False,
    ) -> HarnessResult:
        agent, inputs, run_config, metadata = _create_agent_call(
            prompt=prompt,
            system_prompt=system_prompt,
            config=config,
            skills=skills,
            sandbox=sandbox,
            session_id=session_id,
            python_backend=python_backend,
            tools=tools,
            images=images,
            harness_name=self.name,
            stream=stream,
        )
        if hasattr(agent, "ainvoke"):
            raw = await agent.ainvoke(inputs, config=run_config)
        else:
            raw = await asyncio.to_thread(agent.invoke, inputs, config=run_config)
        return HarnessResult(
            text=_extract_text(raw),
            raw=raw,
            metadata=metadata,
            usage=_extract_usage(raw),
            model=_extract_model(raw, config.model),
        )

    async def stream(
        self,
        *,
        prompt: str,
        system_prompt: str,
        config: PyFlueConfig,
        skills: dict[str, Skill],
        sandbox: Any,
        session_id: str,
        python_backend: Any | None = None,
        tools: list[Any] | tuple[Any, ...] | None = None,
        images: list[Any] | tuple[Any, ...] | None = None,
    ) -> AsyncIterator[PyFlueEvent]:
        agent, inputs, run_config, metadata = _create_agent_call(
            prompt=prompt,
            system_prompt=system_prompt,
            config=config,
            skills=skills,
            sandbox=sandbox,
            session_id=session_id,
            python_backend=python_backend,
            tools=tools,
            images=images,
            harness_name=self.name,
            stream=True,
        )
        if not hasattr(agent, "astream_events"):
            result = await self.run(
                prompt=prompt,
                system_prompt=system_prompt,
                config=config,
                skills=skills,
                sandbox=sandbox,
                session_id=session_id,
                python_backend=python_backend,
                tools=tools,
                images=images,
                stream=True,
            )
            if result.text:
                yield PyFlueEvent("delta", {"text": result.text})
            yield PyFlueEvent("end", {"text": result.text, "metadata": result.metadata})
            return

        chunks: list[str] = []
        raw_end: Any = None
        async for event in agent.astream_events(inputs, config=run_config, version="v2"):
            tool_event = _extract_tool_event(event)
            if tool_event is not None:
                yield tool_event
            delta = _extract_stream_delta(event)
            if delta:
                chunks.append(delta)
                yield PyFlueEvent("delta", {"text": delta, "raw": event})
            if event.get("event") in {"on_chain_end", "on_graph_end"}:
                raw_end = event.get("data", {}).get("output")
        text = "".join(chunks) or _extract_text(raw_end)
        yield PyFlueEvent("end", {"text": text, "metadata": metadata, "raw": raw_end})


class _DeepAgentsSandboxBackend(_DeepAgentsSandboxBase):
    """Adapter from PyFlue sandboxes to DeepAgents' public backend protocol."""

    def __init__(self, sandbox: SandboxBackend):
        self.sandbox = sandbox

    @property
    def id(self) -> str:
        return self.sandbox.id

    def ls(self, path: str) -> Any:
        from deepagents.backends.protocol import LsResult

        try:
            entries = []
            for child in self.sandbox.list_files(_from_backend_path(path)):
                entries.append(
                    {
                        "path": child.path,
                        "is_dir": child.is_dir,
                        "size": child.size,
                    }
                )
            return LsResult(entries=entries)
        except Exception as exc:
            return LsResult(error=str(exc))

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> Any:
        from deepagents.backends.protocol import ReadResult

        try:
            content = self.sandbox.read_file(_from_backend_path(file_path), offset=offset + 1, limit=limit)
            return ReadResult(file_data={"content": content, "encoding": "utf-8"})
        except Exception as exc:
            return ReadResult(error=str(exc))

    def write(self, file_path: str, content: str) -> Any:
        from deepagents.backends.protocol import WriteResult

        try:
            path = _from_backend_path(file_path)
            try:
                self.sandbox.list_files(path)
            except FileNotFoundError:
                pass
            else:
                return WriteResult(
                    error=(
                        f"Cannot write to {file_path} because it already exists. "
                        "Read and then make an edit, or write to a new path."
                    )
                )
            self.sandbox.write_file(path, content)
            return WriteResult(path=file_path)
        except Exception as exc:
            return WriteResult(error=str(exc))

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> Any:
        from deepagents.backends.protocol import EditResult

        try:
            content = self.sandbox.read_file(_from_backend_path(file_path))
            occurrences = content.count(old_string)
            if occurrences == 0:
                return EditResult(error=f"Could not find the text in {file_path}. No changes made.")
            if not replace_all and occurrences > 1:
                return EditResult(
                    error=(
                        f"Found {occurrences} occurrences of the text in {file_path}. "
                        "Provide more surrounding context to make the match unique, or use replace_all."
                    )
                )
            self.sandbox.edit_file(
                _from_backend_path(file_path),
                old_string,
                new_string,
                replace_all=replace_all,
            )
            return EditResult(
                path=file_path,
                occurrences=occurrences if replace_all else 1,
            )
        except Exception as exc:
            return EditResult(error=str(exc))

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None) -> Any:
        from deepagents.backends.protocol import GrepResult

        try:
            output = self.sandbox.grep(pattern, path=_from_backend_path(path or "/"), include=glob)
            matches = []
            for line in output.splitlines():
                file_path, _, rest = line.partition(":")
                line_no, _, text = rest.partition(":")
                if file_path and line_no.isdigit():
                    normalized = file_path if file_path.startswith("/") else "/" + file_path
                    matches.append({"path": normalized, "line": int(line_no), "text": text})
            return GrepResult(matches=matches)
        except Exception as exc:
            return GrepResult(error=str(exc))

    def glob(self, pattern: str, path: str = "/") -> Any:
        from deepagents.backends.protocol import GlobResult

        try:
            base = _from_backend_path(path)
            search = pattern if base == "." else f"{base.rstrip('/')}/{pattern}"
            matches = []
            for item in self.sandbox.glob(search).splitlines():
                matches.append(
                    {
                        "path": item if item.startswith("/") else "/" + item,
                        "is_dir": False,
                        "size": 0,
                    }
                )
            return GlobResult(matches=matches)
        except Exception as exc:
            return GlobResult(error=str(exc))

    def execute(self, command: str, *, timeout: int | None = None) -> Any:
        from deepagents.backends.protocol import ExecuteResponse

        try:
            result = self.sandbox.shell(command, timeout=timeout)
            output = "\n".join(part for part in [result["stdout"], result["stderr"]] if part)
            return ExecuteResponse(output=output.strip(), exit_code=result["exit_code"], truncated=False)
        except Exception as exc:
            return ExecuteResponse(output=str(exc), exit_code=-1, truncated=False)

    def upload_files(self, files: list[tuple[str, bytes]]) -> Any:
        from deepagents.backends.protocol import FileUploadResponse

        responses = []
        for path, content in files:
            try:
                self.sandbox.write_file(
                    _from_backend_path(path),
                    content.decode("utf-8", errors="replace"),
                )
                responses.append(FileUploadResponse(path=path))
            except Exception as exc:
                responses.append(FileUploadResponse(path=path, error=str(exc)))
        return responses

    def download_files(self, paths: list[str]) -> Any:
        from deepagents.backends.protocol import FileDownloadResponse

        responses = []
        for path in paths:
            try:
                content = self.sandbox.read_file(_from_backend_path(path))
                responses.append(FileDownloadResponse(path=path, content=content.encode()))
            except FileNotFoundError:
                responses.append(FileDownloadResponse(path=path, error="file_not_found"))
            except PermissionError:
                responses.append(FileDownloadResponse(path=path, error="permission_denied"))
            except Exception as exc:
                responses.append(FileDownloadResponse(path=path, error=str(exc)))
        return responses


def _create_agent_call(
    *,
    prompt: str,
    system_prompt: str,
    config: PyFlueConfig,
    skills: dict[str, Skill],
    sandbox: Any,
    session_id: str,
    python_backend: Any | None,
    tools: list[Any] | tuple[Any, ...] | None,
    images: list[Any] | tuple[Any, ...] | None = None,
    harness_name: str,
    stream: bool,
) -> tuple[Any, dict[str, Any], dict[str, Any], dict[str, Any]]:
    try:
        from deepagents import create_deep_agent
    except Exception as exc:
        raise ImportError(
            "The DeepAgents backend requires the 'deepagents' package. "
            "Install with: pip install 'pyflue[deepagents]'"
        ) from exc

    backend = _DeepAgentsSandboxBackend(sandbox) if _is_pyflue_sandbox(sandbox) else None
    skill_sources = _skill_sources(config)
    memory = _memory_sources(config)
    permissions = None if backend is not None else _permissions(sandbox)
    agent = create_deep_agent(
        model=_resolve_model(config),
        tools=_tools(python_backend, tools),
        system_prompt=system_prompt or None,
        backend=backend,
        skills=skill_sources or None,
        memory=memory or None,
        permissions=permissions,
        checkpointer=None,
        name="pyflue",
    )
    inputs = {"messages": [{"role": "user", "content": _message_content(prompt, images)}]}
    run_config = {"configurable": {"thread_id": session_id}}
    metadata = {
        "harness": harness_name,
        "model": config.model,
        "thinking_level": config.thinking_level,
        "skill_count": len(skills),
        "skill_sources": skill_sources,
        "memory": memory,
        "stream": stream,
        "tool_count": len(tools or ()),
        "image_count": len(images or ()),
    }
    return agent, inputs, run_config, metadata


def _resolve_model(config: PyFlueConfig) -> Any:
    model = config.model
    if not model:
        return model
    provider_name = model.split(":", 1)[0] if ":" in model else model.split("/", 1)[0]
    settings = config.providers.get(provider_name)
    if settings is None:
        return model
    try:
        from langchain.chat_models import init_chat_model
    except Exception:
        return model

    kwargs: dict[str, Any] = {}
    if settings.base_url:
        kwargs["base_url"] = settings.base_url
    if settings.api_key:
        kwargs["api_key"] = settings.api_key
    if settings.headers:
        kwargs["default_headers"] = settings.headers
    if settings.store_responses and provider_name in {"openai", "azure", "azure-openai"}:
        kwargs["store"] = True
    return init_chat_model(model, **kwargs)


def _skill_sources(config: PyFlueConfig) -> list[str]:
    directory = config.skills_dir or config.root / ".agents" / "skills"
    return ["/" + directory.resolve().relative_to(config.root.resolve()).as_posix()] if directory.exists() else []


def _memory_sources(config: PyFlueConfig) -> list[str]:
    sources = []
    for name in ["AGENTS.md", "CLAUDE.md"]:
        if (config.root / name).exists():
            sources.append(f"/{name}")
    return sources


def _permissions(sandbox: Any) -> Any:
    if not _is_pyflue_sandbox(sandbox):
        return None
    try:
        from deepagents import FilesystemPermission
    except Exception:
        return None
    if getattr(sandbox, "policy", None) and sandbox.policy.allow_write:
        return [FilesystemPermission(operations=["read", "write"], paths=["/**"])]
    return [
        FilesystemPermission(operations=["write"], paths=["/**"], mode="deny"),
        FilesystemPermission(operations=["read"], paths=["/**"]),
    ]


def _tools(
    python_backend: Any | None,
    extra_tools: list[Any] | tuple[Any, ...] | None = None,
) -> list[Any]:
    tools: list[Any] = list(extra_tools or ())
    if python_backend is None:
        return tools

    async def run_code(code: str) -> str:
        """Run Python code in the configured PyFlue Python backend."""
        result = await python_backend.run(code)
        parts = []
        if result.stdout:
            parts.append("stdout:\n" + result.stdout)
        parts.append("result:\n" + repr(result.result))
        if result.stderr:
            parts.append("stderr:\n" + result.stderr)
        return "\n\n".join(parts)

    tools.append(run_code)
    return tools


def _message_content(prompt: str, images: list[Any] | tuple[Any, ...] | None) -> Any:
    if not images:
        return prompt
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for image in images:
        content.append(_image_content_part(image))
    return content


def _image_content_part(image: Any) -> dict[str, Any]:
    if isinstance(image, dict):
        return dict(image)
    data = getattr(image, "data", image)
    mime_type = str(getattr(image, "mime_type", "image/png") or "image/png")
    if isinstance(data, bytes):
        import base64

        data = base64.b64encode(data).decode("ascii")
    data_text = str(data)
    if data_text.startswith(("http://", "https://", "data:")):
        return {"type": "image_url", "image_url": {"url": data_text}}
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime_type};base64,{data_text}"},
    }


def _extract_text(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        messages = raw.get("messages")
        if isinstance(messages, list) and messages:
            last = messages[-1]
            return str(getattr(last, "content", last.get("content") if isinstance(last, dict) else last)).strip()
    for attr in ["output", "final_output", "content", "text"]:
        if hasattr(raw, attr):
            return str(getattr(raw, attr)).strip()
    return str(raw).strip()


def _extract_usage(raw: Any) -> PromptUsage:
    usage = _find_usage(raw)
    if usage is None:
        return PromptUsage()
    input_tokens = _int_attr(usage, "input", "input_tokens", "prompt_tokens")
    output_tokens = _int_attr(usage, "output", "output_tokens", "completion_tokens")
    cache_read = _int_attr(usage, "cache_read", "cacheRead", "cache_read_input_tokens")
    cache_write = _int_attr(usage, "cache_write", "cacheWrite", "cache_creation_input_tokens")
    total_tokens = _int_attr(usage, "total_tokens", "totalTokens", "total")
    if total_tokens == 0:
        total_tokens = input_tokens + output_tokens + cache_read + cache_write
    raw_cost = _get_value(usage, "cost")
    cost = PromptCost()
    if raw_cost is not None:
        cost = PromptCost(
            input=float(_get_value(raw_cost, "input") or 0),
            output=float(_get_value(raw_cost, "output") or 0),
            cache_read=float(_get_value(raw_cost, "cache_read") or _get_value(raw_cost, "cacheRead") or 0),
            cache_write=float(_get_value(raw_cost, "cache_write") or _get_value(raw_cost, "cacheWrite") or 0),
            total=float(_get_value(raw_cost, "total") or 0),
        )
    return PromptUsage(
        input=input_tokens,
        output=output_tokens,
        cache_read=cache_read,
        cache_write=cache_write,
        total_tokens=total_tokens,
        cost=cost,
    )


def _extract_model(raw: Any, fallback: str | None) -> PromptModel:
    model = _find_model(raw) or fallback
    return PromptModel(id=str(model) if model else None)


def _find_usage(value: Any) -> Any | None:
    direct = _get_value(value, "usage")
    if direct is not None:
        return direct
    if isinstance(value, dict):
        messages = value.get("messages")
        if isinstance(messages, list):
            for message in reversed(messages):
                found = _find_usage(message)
                if found is not None:
                    return found
    return None


def _find_model(value: Any) -> Any | None:
    for name in ("model", "model_id", "modelId"):
        candidate = _get_value(value, name)
        if candidate:
            return candidate
    if isinstance(value, dict):
        messages = value.get("messages")
        if isinstance(messages, list):
            for message in reversed(messages):
                found = _find_model(message)
                if found:
                    return found
    return None


def _int_attr(value: Any, *names: str) -> int:
    for name in names:
        candidate = _get_value(value, name)
        if candidate is not None:
            try:
                return int(candidate)
            except (TypeError, ValueError):
                continue
    return 0


def _get_value(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _extract_stream_delta(event: dict[str, Any]) -> str:
    if event.get("event") not in {"on_chat_model_stream", "on_llm_stream"}:
        return ""
    data = event.get("data", {})
    chunk = data.get("chunk")
    content = getattr(chunk, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    text = getattr(chunk, "text", None)
    return text if isinstance(text, str) else ""


def _extract_tool_event(event: dict[str, Any]) -> PyFlueEvent | None:
    event_name = str(event.get("event") or "")
    if event_name not in {"on_tool_start", "on_tool_end"}:
        return None
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    tool_name = str(event.get("name") or data.get("name") or "tool")
    tool_call_id = str(event.get("run_id") or data.get("tool_call_id") or "")
    if event_name == "on_tool_start":
        return PyFlueEvent(
            "tool_start",
            {
                "toolName": tool_name,
                "toolCallId": tool_call_id,
                "args": data.get("input"),
                "raw": event,
            },
        )
    output = data.get("output")
    is_error = bool(data.get("error")) or isinstance(output, Exception)
    return PyFlueEvent(
        "tool_end",
        {
            "toolName": tool_name,
            "toolCallId": tool_call_id,
            "isError": is_error,
            "result": str(output) if isinstance(output, Exception) else output,
            "raw": event,
        },
    )


def _from_backend_path(path: str | None) -> str:
    raw = str(path or "/").strip() or "/"
    if raw in {"/", "/workspace"}:
        return "."
    if raw.startswith("/workspace/"):
        return raw.removeprefix("/workspace/")
    if raw.startswith("/"):
        return raw[1:]
    return raw


def _is_pyflue_sandbox(sandbox: Any) -> bool:
    return all(
        hasattr(sandbox, name)
        for name in [
            "list_files",
            "read_file",
            "write_file",
            "edit_file",
            "grep",
            "glob",
            "shell",
        ]
    )
