"""Tests for the build system."""

from __future__ import annotations

import py_compile

import pytest

from pyflue._builder import BuildOptions, build, resolve_workspace_from_cwd
from pyflue.builder.plugins.cloudflare import CloudflarePlugin
from pyflue.builder.plugins.cloudrun import CloudRunPlugin
from pyflue.builder.plugins.docker import DockerPlugin
from pyflue.builder.plugins.lambda_ import LambdaPlugin
from pyflue.builder.plugins.providers import (
    FlyPlugin,
    NetlifyPlugin,
    RailwayPlugin,
    RenderPlugin,
    VercelPlugin,
)
from pyflue.builder.plugins.uvicorn import UvicornPlugin
from pyflue.types import AgentInfo, BuildContext, PyFlueConfig


def test_resolve_workspace_from_cwd_finds_agents_dir(tmp_path):
    """Test workspace resolution finds agents directory."""
    (tmp_path / "agents").mkdir()
    result = resolve_workspace_from_cwd(tmp_path)
    assert result == tmp_path


def test_resolve_workspace_from_cwd_finds_dot_agents(tmp_path):
    """Test workspace resolution finds .agents directory."""
    (tmp_path / ".agents").mkdir()
    result = resolve_workspace_from_cwd(tmp_path)
    assert result == tmp_path


def test_resolve_workspace_from_cwd_returns_none_when_empty(tmp_path):
    """Test workspace resolution returns None for empty directory."""
    result = resolve_workspace_from_cwd(tmp_path)
    assert result is None


def test_uvicorn_plugin_generates_entry_point(tmp_path):
    """Test UvicornPlugin generates valid server code."""
    plugin = UvicornPlugin()
    ctx = BuildContext(
        agents=[
            AgentInfo(name="hello", file_path=tmp_path / "hello.py", triggers={"webhook": True}),
            AgentInfo(name="world", file_path=tmp_path / "world.py", triggers={}),
        ],
        roles={},
        workspace_dir=tmp_path,
        output_dir=tmp_path,
        config=PyFlueConfig(),
    )

    code = plugin.generate_entry_point(ctx)
    assert "create_app" in code
    assert "uvicorn.run" in code
    assert "PORT" in code


def test_uvicorn_plugin_additional_outputs():
    """Test UvicornPlugin generates requirements.txt."""
    plugin = UvicornPlugin()
    ctx = BuildContext(
        agents=[],
        roles={},
        workspace_dir=None,
        output_dir=None,
        config=PyFlueConfig(),
    )

    outputs = plugin.additional_outputs(ctx)
    assert "requirements.txt" in outputs
    assert "fastapi" in outputs["requirements.txt"]


def test_lambda_plugin_generates_entry_point(tmp_path):
    """Test LambdaPlugin generates valid handler code."""
    plugin = LambdaPlugin()
    ctx = BuildContext(
        agents=[
            AgentInfo(name="hello", file_path=tmp_path / "hello.py", triggers={"webhook": True}),
        ],
        roles={},
        workspace_dir=tmp_path,
        output_dir=tmp_path,
        config=PyFlueConfig(),
    )

    code = plugin.generate_entry_point(ctx)
    assert "Mangum" in code
    assert "create_app" in code
    assert "handler = Mangum" in code


def test_lambda_plugin_additional_outputs():
    """Test LambdaPlugin generates requirements.txt."""
    plugin = LambdaPlugin()
    ctx = BuildContext(
        agents=[],
        roles={},
        workspace_dir=None,
        output_dir=None,
        config=PyFlueConfig(),
    )

    outputs = plugin.additional_outputs(ctx)
    assert "requirements.txt" in outputs
    assert "mangum" in outputs["requirements.txt"]


def test_docker_plugin_generates_entry_point():
    """Test DockerPlugin generates server code."""
    plugin = DockerPlugin()
    ctx = BuildContext(
        agents=[],
        roles={},
        workspace_dir=None,
        output_dir=None,
        config=PyFlueConfig(),
    )

    code = plugin.generate_entry_point(ctx)
    assert "create_app" in code


def test_docker_plugin_additional_outputs():
    """Test DockerPlugin generates Dockerfile and requirements."""
    plugin = DockerPlugin()
    ctx = BuildContext(
        agents=[],
        roles={},
        workspace_dir=None,
        output_dir=None,
        config=PyFlueConfig(),
    )

    outputs = plugin.additional_outputs(ctx)
    assert "Dockerfile" in outputs
    assert "requirements.txt" in outputs
    assert ".dockerignore" in outputs
    assert "FROM python" in outputs["Dockerfile"]
    assert 'CMD ["python", "server.py"]' in outputs["Dockerfile"]


def test_cloudrun_plugin_generates_entry_point():
    """Test CloudRunPlugin generates server code."""
    plugin = CloudRunPlugin()
    ctx = BuildContext(
        agents=[],
        roles={},
        workspace_dir=None,
        output_dir=None,
        config=PyFlueConfig(),
    )

    code = plugin.generate_entry_point(ctx)
    assert "create_app" in code


def test_cloudrun_plugin_additional_outputs():
    """Test CloudRunPlugin generates Dockerfile and cloudbuild.yaml."""
    plugin = CloudRunPlugin()
    ctx = BuildContext(
        agents=[],
        roles={},
        workspace_dir=None,
        output_dir=None,
        config=PyFlueConfig(),
    )

    outputs = plugin.additional_outputs(ctx)
    assert "Dockerfile" in outputs
    assert "requirements.txt" in outputs
    assert "cloudbuild.yaml" in outputs
    assert "gunicorn" in outputs["Dockerfile"]


def test_cloudflare_plugin_generates_container_worker_outputs(tmp_path):
    plugin = CloudflarePlugin()
    ctx = BuildContext(
        agents=[
            AgentInfo(name="hello", file_path=tmp_path / "hello.py", triggers={"webhook": True}),
            AgentInfo(name="ops", file_path=tmp_path / "ops.py", triggers={}),
        ],
        roles={},
        workspace_dir=tmp_path,
        output_dir=tmp_path,
        config=PyFlueConfig(),
    )

    code = plugin.generate_entry_point(ctx)
    outputs = plugin.additional_outputs(ctx)

    assert "create_app" in code
    assert "worker.ts" in outputs
    assert "wrangler.jsonc" in outputs
    assert "package.json" in outputs
    assert "class PyFlueContainer extends Container" in outputs["worker.ts"]
    assert '"max_instances": 2' in outputs["wrangler.jsonc"]
    assert "@cloudflare/containers" in outputs["package.json"]


@pytest.mark.parametrize(
    ("plugin_cls", "expected_file", "expected_text"),
    [
        (RailwayPlugin, "railway.json", '"builder": "DOCKERFILE"'),
        (RenderPlugin, "render.yaml", "runtime: docker"),
        (FlyPlugin, "fly.toml", "[http_service]"),
        (VercelPlugin, "vercel.json", '"dest": "server.py"'),
        (NetlifyPlugin, "netlify.toml", "netlify/functions"),
    ],
)
def test_provider_plugins_generate_provider_outputs(plugin_cls, expected_file, expected_text):
    plugin = plugin_cls()
    ctx = BuildContext(
        agents=[],
        roles={},
        workspace_dir=None,
        output_dir=None,
        config=PyFlueConfig(),
    )

    code = plugin.generate_entry_point(ctx)
    outputs = plugin.additional_outputs(ctx)

    assert "create_app" in code
    assert expected_file in outputs
    assert expected_text in outputs[expected_file]
    assert "requirements.txt" in outputs


@pytest.mark.asyncio
async def test_build_with_workspace(tmp_path):
    """Test build function with a proper workspace."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()

    (agents_dir / "hello.py").write_text(
        'triggers = {"webhook": True}\n'
        "async def default(context):\n"
        "    return {'message': 'hello'}\n",
        encoding="utf-8",
    )

    (tmp_path / "roles").mkdir()
    (tmp_path / "roles" / "coder.md").write_text(
        "---\nname: coder\ndescription: Coding role\n---\nYou are a coder.",
        encoding="utf-8",
    )

    result = build(BuildOptions(
        workspace_dir=str(tmp_path),
        output_dir=str(tmp_path),
        target="uvicorn",
    ))

    assert result.changed is True
    assert (tmp_path / "dist" / "server.py").exists()
    py_compile.compile(tmp_path / "dist" / "server.py", doraise=True)
    assert (tmp_path / "dist" / "manifest.json").exists()
    assert (tmp_path / "dist" / "requirements.txt").exists()


@pytest.mark.asyncio
async def test_build_docker_generates_matching_server_entry(tmp_path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "hello.py").write_text(
        "triggers = {'webhook': True}\n"
        "async def default(context):\n"
        "    return {'message': 'hello'}\n",
        encoding="utf-8",
    )

    result = build(BuildOptions(
        workspace_dir=str(tmp_path),
        output_dir=str(tmp_path),
        target="docker",
    ))

    generated = {path.name for path in result.generated_files}
    assert "server.py" in generated
    assert "Dockerfile" in generated
    assert 'CMD ["python", "server.py"]' in (tmp_path / "dist" / "Dockerfile").read_text()
    py_compile.compile(tmp_path / "dist" / "server.py", doraise=True)


@pytest.mark.asyncio
async def test_build_cloudflare_generates_worker_and_container_files(tmp_path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "hello.py").write_text(
        "triggers = {'webhook': True}\n"
        "async def default(context):\n"
        "    return {'message': 'hello'}\n",
        encoding="utf-8",
    )

    result = build(BuildOptions(
        workspace_dir=str(tmp_path),
        output_dir=str(tmp_path),
        target="cloudflare",
    ))

    generated = {path.name for path in result.generated_files}
    assert {"server.py", "Dockerfile", "worker.ts", "wrangler.jsonc", "package.json"} <= generated
    assert "PyFlueContainer" in (tmp_path / "dist" / "worker.ts").read_text()
    assert '"main": "worker.ts"' in (tmp_path / "dist" / "wrangler.jsonc").read_text()
    assert "@cloudflare/containers" in (tmp_path / "dist" / "package.json").read_text()
    py_compile.compile(tmp_path / "dist" / "server.py", doraise=True)


@pytest.mark.parametrize(
    ("target", "provider_file"),
    [
        ("railway", "railway.json"),
        ("render", "render.yaml"),
        ("fly", "fly.toml"),
        ("vercel", "vercel.json"),
        ("netlify", "netlify.toml"),
    ],
)
def test_build_provider_targets_use_workspace_build_system(tmp_path, target, provider_file):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "hello.py").write_text(
        "triggers = {'webhook': True}\n"
        "async def default(context):\n"
        "    return {'message': 'hello'}\n",
        encoding="utf-8",
    )

    result = build(BuildOptions(
        workspace_dir=str(tmp_path),
        output_dir=str(tmp_path),
        target=target,
    ))

    generated = {path.name for path in result.generated_files}
    assert "server.py" in generated
    assert provider_file in generated
    assert (tmp_path / "dist" / provider_file).exists()
    py_compile.compile(tmp_path / "dist" / "server.py", doraise=True)


@pytest.mark.asyncio
async def test_build_lambda_entry_compiles(tmp_path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "hello.py").write_text(
        "triggers = {'webhook': True}\n"
        "async def agent(context):\n"
        "    return {'message': 'hello'}\n",
        encoding="utf-8",
    )

    build(BuildOptions(
        workspace_dir=str(tmp_path),
        output_dir=str(tmp_path),
        target="lambda",
    ))

    py_compile.compile(tmp_path / "dist" / "main.py", doraise=True)


@pytest.mark.asyncio
async def test_build_discovers_nested_agents(tmp_path):
    agents_dir = tmp_path / "agents" / "nested"
    agents_dir.mkdir(parents=True)
    (agents_dir / "hello.py").write_text(
        'triggers = {"webhook": True}\n'
        "async def default(context):\n"
        "    return {'message': 'hello'}\n",
        encoding="utf-8",
    )

    build(BuildOptions(
        workspace_dir=str(tmp_path),
        output_dir=str(tmp_path),
        target="uvicorn",
    ))

    import json
    manifest = json.loads((tmp_path / "dist" / "manifest.json").read_text())
    assert manifest["agents"][0]["name"] == "nested.hello"
    assert manifest["agents"][0]["triggers"]["webhook"] is True


@pytest.mark.asyncio
async def test_build_generates_manifest(tmp_path):
    """Test build generates proper manifest.json."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()

    (agents_dir / "hello.py").write_text(
        "triggers = {'webhook': True, 'cron': '0 * * * *'}\n"
        "async def default(context):\n"
        "    return {'message': 'hello'}\n",
        encoding="utf-8",
    )

    build(BuildOptions(
        workspace_dir=str(tmp_path),
        output_dir=str(tmp_path),
        target="uvicorn",
    ))

    import json
    manifest = json.loads((tmp_path / "dist" / "manifest.json").read_text())
    assert len(manifest["agents"]) == 1
    assert manifest["agents"][0]["name"] == "hello"
    assert manifest["agents"][0]["triggers"]["webhook"] is True


@pytest.mark.asyncio
async def test_build_fails_without_agents(tmp_path):
    """Test build fails when no agents found."""
    with pytest.raises(ValueError, match="No agent files found"):
        build(BuildOptions(
            workspace_dir=str(tmp_path),
            output_dir=str(tmp_path),
            target="uvicorn",
        ))


@pytest.mark.asyncio
async def test_build_fails_without_target():
    """Test build fails when no target specified."""
    with pytest.raises(ValueError, match="No build target specified"):
        build(BuildOptions(
            workspace_dir=".",
            output_dir=".",
            target=None,
        ))
