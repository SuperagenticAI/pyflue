"""Build plugins for PyFlue deployment targets."""

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

__all__ = [
    "UvicornPlugin",
    "LambdaPlugin",
    "DockerPlugin",
    "CloudRunPlugin",
    "RailwayPlugin",
    "RenderPlugin",
    "FlyPlugin",
    "VercelPlugin",
    "NetlifyPlugin",
    "CloudflarePlugin",
]
