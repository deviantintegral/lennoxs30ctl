"""CLI and TUI for Lennox S30/E30/M30 thermostats over the local API."""

from __future__ import annotations

from lennoxs30ctl.config import ConfigError, Settings, resolve
from lennoxs30ctl.connection import ConnectionError_, ConnectionState, S30Connection
from lennoxs30ctl.format import SystemView, ZoneView, system_view, zone_view

__version__ = "0.1.0"

__all__ = [
    "ConfigError",
    "ConnectionError_",
    "ConnectionState",
    "S30Connection",
    "Settings",
    "SystemView",
    "ZoneView",
    "__version__",
    "resolve",
    "system_view",
    "zone_view",
]
