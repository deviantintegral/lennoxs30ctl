"""Textual TUI for lennoxs30ctl."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lennoxs30ctl.config import Settings

__all__ = ["run_tui"]


async def run_tui(settings: Settings, *, log_level: int = logging.WARNING) -> None:
    """Connect to the thermostat and run the dashboard."""
    from lennoxs30ctl.connection import S30Connection
    from lennoxs30ctl.tui.app import LennoxS30App

    connection = S30Connection(
        settings.host,
        settings.app_id,
        message_logging=log_level <= logging.DEBUG,
    )
    await connection.connect()
    try:
        await LennoxS30App(connection).run_async()
    finally:
        await connection.close()
