from __future__ import annotations

from pyflue.config import load_config


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
