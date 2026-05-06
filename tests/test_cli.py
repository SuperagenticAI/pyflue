from __future__ import annotations

from typer.testing import CliRunner

from pyflue.cli import app


def test_cli_init_and_skill_new(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(app, ["init", "demo"])
    assert result.exit_code == 0
    assert (tmp_path / "demo" / "pyflue.toml").exists()
    assert (tmp_path / "demo" / ".agents" / "skills" / "triage.md").exists()
    assert (tmp_path / "demo" / ".agents" / "roles" / "coder.md").exists()
    assert (tmp_path / "demo" / "agents" / "default.py").exists()

    monkeypatch.chdir(tmp_path / "demo")
    result = runner.invoke(app, ["skill", "new", "review"])
    assert result.exit_code == 0
    assert (tmp_path / "demo" / ".agents" / "skills" / "review.md").exists()


def test_cli_build_targets(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(app, ["build"])
    assert result.exit_code == 0
    assert (tmp_path / "Dockerfile").exists()
    assert (tmp_path / "app.py").exists()

    result = runner.invoke(app, ["build", "--target", "github-actions"])
    assert result.exit_code == 0
    assert (tmp_path / ".github" / "workflows" / "pyflue-agent.yml").exists()

    result = runner.invoke(app, ["build", "--target", "gitlab-ci"])
    assert result.exit_code == 0
    assert (tmp_path / ".gitlab-ci.yml").exists()

    result = runner.invoke(app, ["build", "--target", "railway"])
    assert result.exit_code == 0
    assert (tmp_path / "railway.json").exists()

    result = runner.invoke(app, ["build", "--target", "render"])
    assert result.exit_code == 0
    assert (tmp_path / "render.yaml").exists()

    result = runner.invoke(app, ["build", "--target", "fly"])
    assert result.exit_code == 0
    assert (tmp_path / "fly.toml").exists()

    result = runner.invoke(app, ["build", "--target", "vercel"])
    assert result.exit_code == 0
    assert (tmp_path / "vercel.json").exists()

    result = runner.invoke(app, ["build", "--target", "netlify"])
    assert result.exit_code == 0
    assert (tmp_path / "netlify.toml").exists()

    result = runner.invoke(app, ["build", "--target", "cloudflare"])
    assert result.exit_code == 0
    assert (tmp_path / "wrangler.toml").exists()


def test_cli_deploy_writes_manifest(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(app, ["deploy", "--dry-run"])

    assert result.exit_code == 0
    assert (tmp_path / ".pyflue" / "deploy.json").exists()


def test_cli_add_lists_and_prints_connector_guides():
    runner = CliRunner()

    result = runner.invoke(app, ["add"])
    assert result.exit_code == 0
    assert "pyflue add daytona" in result.output
    assert "pyflue add https://provider.example/docs" in result.output

    result = runner.invoke(app, ["add", "daytona"])
    assert result.exit_code == 0
    assert "pyflue add daytona --category sandbox --print | codex" in result.output

    result = runner.invoke(app, ["add", "daytona", "--print"])
    assert result.exit_code == 0
    assert "PyFlue already includes this provider" in result.output
    assert 'sandbox = "daytona"' in result.output

    result = runner.invoke(app, ["add", "https://e2b.dev/docs", "--print"])
    assert result.exit_code == 0
    assert "Build a PyFlue Sandbox Connector" in result.output
    assert "https://e2b.dev/docs" in result.output


def test_cli_routes_lists_agent_routes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "default.py").write_text(
        "triggers = {'webhook': True}\n"
        "async def default(context):\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    runner = CliRunner()

    result = runner.invoke(app, ["routes"])

    assert result.exit_code == 0
    assert '"name": "default"' in result.output
    assert '"/agents/default/{agent_id}"' in result.output
