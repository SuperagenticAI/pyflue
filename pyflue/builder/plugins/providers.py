"""Provider-specific build plugins."""

from __future__ import annotations

import json
from typing import Any

from pyflue.builder.plugins.docker import DockerPlugin
from pyflue.builder.plugins.uvicorn import UvicornPlugin
from pyflue.types import BuildContext


class RailwayPlugin(DockerPlugin):
    """Build plugin for Railway Docker deployments."""

    name = "railway"

    def additional_outputs(self, ctx: BuildContext) -> dict[str, str]:
        outputs = super().additional_outputs(ctx)
        outputs["railway.json"] = json.dumps(
            {
                "$schema": "https://railway.app/railway.schema.json",
                "build": {"builder": "DOCKERFILE"},
                "deploy": {"startCommand": "python server.py"},
            },
            indent=2,
        ) + "\n"
        return outputs


class RenderPlugin(DockerPlugin):
    """Build plugin for Render Docker services."""

    name = "render"

    def additional_outputs(self, ctx: BuildContext) -> dict[str, str]:
        outputs = super().additional_outputs(ctx)
        outputs["render.yaml"] = (
            "services:\n"
            "  - type: web\n"
            "    name: pyflue-agent\n"
            "    runtime: docker\n"
            "    plan: starter\n"
            "    envVars:\n"
            "      - key: PORT\n"
            "        value: 8000\n"
        )
        return outputs


class FlyPlugin(DockerPlugin):
    """Build plugin for Fly.io Docker deployments."""

    name = "fly"

    def additional_outputs(self, ctx: BuildContext) -> dict[str, str]:
        outputs = super().additional_outputs(ctx)
        outputs["fly.toml"] = (
            'app = "pyflue-agent"\n'
            'primary_region = "iad"\n\n'
            "[http_service]\n"
            "  internal_port = 8000\n"
            "  force_https = true\n"
            "  auto_stop_machines = true\n"
            "  auto_start_machines = true\n"
        )
        return outputs


class VercelPlugin(UvicornPlugin):
    """Build plugin for Vercel Python deployments."""

    name = "vercel"

    def additional_outputs(self, ctx: BuildContext) -> dict[str, str]:
        outputs = super().additional_outputs(ctx)
        outputs["vercel.json"] = json.dumps(
            {
                "builds": [{"src": "server.py", "use": "@vercel/python"}],
                "routes": [{"src": "/(.*)", "dest": "server.py"}],
            },
            indent=2,
        ) + "\n"
        return outputs


class NetlifyPlugin(UvicornPlugin):
    """Build plugin for Netlify Python function scaffolding."""

    name = "netlify"

    def additional_outputs(self, ctx: BuildContext) -> dict[str, str]:
        outputs = super().additional_outputs(ctx)
        outputs["netlify.toml"] = (
            "[build]\n"
            "  command = \"pip install -r requirements.txt\"\n"
            "  publish = \".\"\n\n"
            "[functions]\n"
            "  directory = \"netlify/functions\"\n"
        )
        outputs["netlify/functions/pyflue.py"] = (
            '"""Auto-generated Netlify function entry for PyFlue."""\n'
            "from server import app\n\n"
            "__all__ = [\"app\"]\n"
        )
        return outputs

    def esbuild_options(self, ctx: BuildContext) -> dict[str, Any]:
        return {}
