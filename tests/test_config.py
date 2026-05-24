from __future__ import annotations

from pyflue.config import define_config, load_config


def test_load_config_parses_providers_compaction_and_mcp(tmp_path):
    config_path = tmp_path / "pyflue.toml"
    config_path.write_text(
        """
[agent]
model = "openai:gpt-4o"
thinking_level = "high"
max_task_depth = 4

[providers.openai]
base_url = "https://gateway.example/v1"
api_key = "test-key"
store_responses = true

[providers.openai.headers]
X-Team = "agents"

[compaction]
enabled = false
context_window_tokens = 1000
reserve_tokens = 100
keep_recent_tokens = 200

[mcp]
mode = "search_execute"
search_limit = 3
search_backend = "bm25"

[mcp.servers.docs]
transport = "stdio"
command = "python"
args = ["server.py"]
""",
        encoding="utf-8",
    )

    config = load_config(config_path)

    provider = config.providers.get("openai")
    assert provider is not None
    assert provider.base_url == "https://gateway.example/v1"
    assert provider.api_key == "test-key"
    assert provider.store_responses is True
    assert provider.headers == {"X-Team": "agents"}
    assert config.thinking_level == "high"
    assert config.compaction.enabled is False
    assert config.compaction.context_window_tokens == 1000
    assert config.compaction.reserve_tokens == 100
    assert config.compaction.keep_recent_tokens == 200
    assert config.max_task_depth == 4
    assert config.mcp is not None
    assert config.mcp.mode == "search_execute"
    assert config.mcp.search_limit == 3
    assert config.mcp.servers["docs"]["command"] == "python"
    assert config.config_path == config_path.resolve()


def test_define_config_identity():
    value = {"agent": {"model": "test"}}

    assert define_config(value) is value


def test_load_python_config_explicit_path(tmp_path):
    config_path = tmp_path / "pyflue.config.py"
    config_path.write_text(
        "from pyflue import define_config\n\n"
        "config = define_config({\n"
        "    'agent': {\n"
        "        'model': 'openai:gpt-4o-mini',\n"
        "        'harness': 'pydanticai',\n"
        "        'sandbox': 'virtual',\n"
        "        'agents_dir': 'workers',\n"
        "        'allowed_commands': ['python'],\n"
        "        'max_task_depth': 2,\n"
        "    },\n"
        "    'providers': {\n"
        "        'openai': {'base_url': 'https://gateway.example/v1'},\n"
        "    },\n"
        "})\n",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.config_path == config_path.resolve()
    assert config.root == tmp_path.resolve()
    assert config.model == "openai:gpt-4o-mini"
    assert config.harness == "pydanticai"
    assert config.agents_dir == (tmp_path / "workers").resolve()
    assert config.allowed_commands == ("python",)
    assert config.max_task_depth == 2
    assert config.providers.get("openai").base_url == "https://gateway.example/v1"


def test_load_config_falls_back_to_python_config_when_toml_missing(tmp_path, monkeypatch):
    (tmp_path / "pyflue.config.py").write_text(
        "CONFIG = {'agent': {'model': 'fallback-model'}}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    config = load_config()

    assert config.config_path == (tmp_path / "pyflue.config.py").resolve()
    assert config.model == "fallback-model"
