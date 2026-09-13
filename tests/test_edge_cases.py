"""Tests for the paths the happy-path tests do not reach."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from textual.widgets import Input, OptionList, RichLog

from lennoxs30ctl.cli import _log_level, build_parser, cmd_tui
from lennoxs30ctl.config import Settings
from lennoxs30ctl.connection import S30Connection
from lennoxs30ctl.format import _opt_bool, _opt_float, _opt_int, _opt_str, zone_view
from lennoxs30ctl.tui.app import LennoxS30App, _get_zone_commands
from lennoxs30ctl.tui.dialogs import ChoiceScreen, ValueScreen
from lennoxs30ctl.tui.screens import _TuiLogHandler
from lennoxs30ctl.tui.widgets import ArrowNavMixin


def _app(connection: S30Connection) -> LennoxS30App:
    """The real app with the background pump stubbed out."""
    connection.start_pump = lambda: None  # type: ignore[method-assign]
    return LennoxS30App(connection)


class TestCoercion:
    """The library can hand back values that will not convert."""

    def test_float_of_nonsense(self) -> None:
        assert _opt_float("warm") is None
        assert _opt_float(object()) is None
        assert _opt_float(None) is None
        assert _opt_float("19.5") == 19.5

    def test_int_of_nonsense(self) -> None:
        assert _opt_int("many") is None
        assert _opt_int(object()) is None
        assert _opt_int(None) is None
        assert _opt_int("40") == 40

    def test_str(self) -> None:
        assert _opt_str(None) is None
        assert _opt_str("") is None
        assert _opt_str(7) == "7"

    def test_bool(self) -> None:
        assert _opt_bool(None) is None
        assert _opt_bool(0) is False
        assert _opt_bool(1) is True


class TestZonePresetEdges:
    """A zone that is not running any schedule."""

    def test_no_schedule_id(self, system: Any, zone: Any) -> None:
        zone.scheduleId = None
        assert zone_view(system, zone).preset is None

    def test_schedule_without_a_name(self, api_with_schedules: Any) -> None:
        system = api_with_schedules.system_list[0]
        zone = system.zone_list[0]
        zone.scheduleId = 1
        system.getSchedule(1).name = None
        assert zone_view(system, zone).preset is None


class TestLogLevels:
    """Verbosity flags map onto logging levels."""

    def test_default(self) -> None:
        assert _log_level(build_parser().parse_args(["list"])) == logging.WARNING

    def test_verbose(self) -> None:
        assert _log_level(build_parser().parse_args(["-v", "list"])) == logging.INFO

    def test_debug(self) -> None:
        args = build_parser().parse_args(["--debug", "list"])
        assert _log_level(args) == logging.DEBUG


class TestTuiExtraMissing:
    """The TUI is an optional extra."""

    async def test_import_error_explains_the_extra(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with (
            patch.dict("sys.modules", {"lennoxs30ctl.tui": None}),
            pytest.raises(SystemExit) as exc,
        ):
            await cmd_tui(Settings("host", "id"), log_level=logging.WARNING)
        assert exc.value.code == 1
        assert "tui" in capsys.readouterr().out

    async def test_launches_when_available(self) -> None:
        with patch("lennoxs30ctl.tui.run_tui", new=AsyncMock()) as mock_run:
            await cmd_tui(Settings("host", "id"), log_level=logging.WARNING)
        mock_run.assert_awaited_once()


class TestLogHandler:
    """The messages panel handler must never take the app down."""

    def test_broken_widget_is_handled(self) -> None:
        rich_log = MagicMock(spec=RichLog)
        rich_log.write.side_effect = RuntimeError("no")
        handler = _TuiLogHandler(rich_log)
        handler.handleError = MagicMock()  # type: ignore[method-assign]
        handler.emit(
            logging.LogRecord("x", logging.INFO, __file__, 1, "hi", None, None)
        )
        handler.handleError.assert_called_once()


class TestArrowNav:
    """The mixin ignores anything that is not a key event."""

    def test_non_key_event_is_ignored(self) -> None:
        mixin = ArrowNavMixin()
        mixin.on_key(object())


class TestPaletteProvider:
    """The palette provider factory."""

    def test_returns_the_class(self) -> None:
        from lennoxs30ctl.tui.app import ZoneCommandsProvider

        assert _get_zone_commands() is ZoneCommandsProvider


class TestActionGuards:
    """Actions do nothing sensible when there is no dashboard yet."""

    async def test_actions_before_the_dashboard_exists(
        self, connection: S30Connection
    ) -> None:
        app = _app(connection)
        # No dashboard has been pushed, so every action should be inert.
        app.action_set_heat()
        app.action_set_cool()
        app.action_set_single()
        app.action_set_hvac_mode()
        app.action_set_fan_mode()
        app.action_set_humidity_mode()
        app.action_set_humidify()
        app.action_set_dehumidify()
        app.action_set_preset()
        app.action_toggle_away()
        app.action_cancel_hold()
        app.action_switch_zone()
        assert app.dashboard is None

    async def test_away_without_a_system_view(self, connection: S30Connection) -> None:
        app = _app(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            dashboard = app.dashboard
            assert dashboard is not None
            dashboard._system_view = None
            connection.system().set_manual_away_mode = AsyncMock()
            app.action_toggle_away()
            await pilot.pause()
        connection.system().set_manual_away_mode.assert_not_awaited()


class TestRemainingActions:
    """Actions the main suite does not exercise."""

    async def test_set_cool_writes(self, connection: S30Connection) -> None:
        zone = connection.system().zone_list[0]
        zone.perform_setpoint = AsyncMock()
        app = _app(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("c")
            await pilot.pause()
            app.screen.query_one("#value-input", Input).value = "24"
            await pilot.click("#set-btn")
            await pilot.pause()
            await pilot.pause()
        zone.perform_setpoint.assert_awaited_once_with(r_cspC=24.0)

    async def test_set_cool_refused_in_single_mode(
        self, connection: S30Connection
    ) -> None:
        connection.system().single_setpoint_mode = True
        app = _app(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("c")
            await pilot.pause()
            assert not isinstance(app.screen, ValueScreen)

    async def test_set_single_writes(self, connection: S30Connection) -> None:
        connection.system().single_setpoint_mode = True
        zone = connection.system().zone_list[0]
        zone.perform_setpoint = AsyncMock()
        app = _app(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("t")
            await pilot.pause()
            app.screen.query_one("#value-input", Input).value = "21"
            await pilot.click("#set-btn")
            await pilot.pause()
            await pilot.pause()
        zone.perform_setpoint.assert_awaited_once_with(r_spC=21.0)

    async def test_setpoint_in_fahrenheit(self, connection: S30Connection) -> None:
        connection.system().temperatureUnit = "F"
        zone = connection.system().zone_list[0]
        zone.perform_setpoint = AsyncMock()
        app = _app(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("h")
            await pilot.pause()
            app.screen.query_one("#value-input", Input).value = "68"
            await pilot.click("#set-btn")
            await pilot.pause()
            await pilot.pause()
        zone.perform_setpoint.assert_awaited_once_with(r_hsp=68.0)

    async def test_humidity_mode_writes(self, connection: S30Connection) -> None:
        zone = connection.system().zone_list[0]
        zone.humidificationOption = True
        zone.dehumidificationOption = True
        zone.setHumidityMode = AsyncMock()
        app = _app(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("d")
            await pilot.pause()
            app.screen.query_one("#choice-list", OptionList).highlighted = 1
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
        zone.setHumidityMode.assert_awaited_once_with("dehumidify")

    async def test_hvac_mode_writes(self, connection: S30Connection) -> None:
        zone = connection.system().zone_list[0]
        zone.setHVACMode = AsyncMock()
        app = _app(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("m")
            await pilot.pause()
            app.screen.query_one("#choice-list", OptionList).highlighted = 2
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
        zone.setHVACMode.assert_awaited_once_with("heat")

    async def test_preset_by_name_writes(self, api_with_schedules: Any) -> None:
        """A real schedule name, rather than the manual mode entry."""
        conn = S30Connection("host", "id", api=api_with_schedules)
        conn.start_pump = lambda: None  # type: ignore[method-assign]
        system = api_with_schedules.system_list[0]
        zone = system.zone_list[0]
        zone.setSchedule = AsyncMock()
        app = LennoxS30App(conn)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("p")
            await pilot.pause()
            app.screen.query_one("#choice-list", OptionList).highlighted = 1
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
        zone.setSchedule.assert_awaited_once_with("summer")

    async def test_dialogs_cancel_without_writing(
        self, connection: S30Connection
    ) -> None:
        zone = connection.system().zone_list[0]
        zone.setFanMode = AsyncMock()
        app = _app(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("f")
            await pilot.pause()
            assert isinstance(app.screen, ChoiceScreen)
            await pilot.press("escape")
            await pilot.pause()
        zone.setFanMode.assert_not_awaited()

    async def test_humidify_cancel(self, connection: S30Connection) -> None:
        zone = connection.system().zone_list[0]
        zone.perform_humidify_setpoint = AsyncMock()
        app = _app(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("u")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
        zone.perform_humidify_setpoint.assert_not_awaited()

    async def test_switch_zone_cancel(self, connection: S30Connection) -> None:
        app = _app(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            dashboard = app.dashboard
            assert dashboard is not None
            before = dashboard.selected_zone
            await pilot.press("z")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert dashboard.selected_zone == before

    async def test_switch_zone_to_an_unknown_name(
        self, connection: S30Connection
    ) -> None:
        app = _app(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            dashboard = app.dashboard
            assert dashboard is not None
            before = dashboard.selected_zone
            app.action_switch_zone()
            await pilot.pause()
            app.screen.dismiss("Nowhere")
            await pilot.pause()
            assert dashboard.selected_zone == before
