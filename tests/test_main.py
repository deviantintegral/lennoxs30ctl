"""Tests for the ``python -m lennoxs30ctl`` entry point."""

from __future__ import annotations

import runpy
from unittest.mock import patch


class TestMainEntryPoint:
    """Running the module invokes the CLI."""

    def test_main_calls_cli_main(self) -> None:
        with patch("lennoxs30ctl.cli.main") as mock_main:
            runpy.run_module("lennoxs30ctl", run_name="__main__")
        mock_main.assert_called_once()
