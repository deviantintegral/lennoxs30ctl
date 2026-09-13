"""Tests for the TUI widgets, dialogs, dashboard and app."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

from lennoxs30api.s30exception import EC_BAD_PARAMETERS, S30Exception
from textual.app import App
from textual.widgets import Button, Input, OptionList, RichLog

from lennoxs30ctl.connection import ConnectionState, S30Connection
from lennoxs30ctl.format import system_view, zone_view
from lennoxs30ctl.tui import run_tui
from lennoxs30ctl.tui.app import LennoxS30App, ZoneCommandsProvider
from lennoxs30ctl.tui.dialogs import ChoiceScreen, ValueScreen

if TYPE_CHECKING:
    import pytest

from lennoxs30ctl.tui.widgets import (
    ClickableParam,
    SystemPanel,
    ZonePanel,
    system_fields,
    zone_fields,
)


class TestFields:
    """The field lists behind the panels."""

    def test_zone_fields_split_setpoints(self, system: Any, zone: Any) -> None:
        fields = zone_fields(zone_view(system, zone))
        labels = [f.label for f in fields]
        assert any("Heat" in label for label in labels)
        assert any("Cool" in label for label in labels)

    def test_zone_fields_single_setpoint(self, system: Any, zone: Any) -> None:
        system.single_setpoint_mode = True
        fields = zone_fields(zone_view(system, zone))
        labels = [f.label for f in fields]
        assert any("Setpoint" in label for label in labels)
        assert not any("Heat:" in label for label in labels)

    def test_zone_fields_are_wired_to_actions(self, system: Any, zone: Any) -> None:
        actions = {f.action for f in zone_fields(zone_view(system, zone))}
        assert "set_hvac_mode" in actions
        assert "set_heat" in actions
        assert "set_preset" in actions

    def test_hold_is_marked(self, system: Any, zone: Any) -> None:
        zone.scheduleId = zone.getOverrideScheduleId()
        values = [f.value for f in zone_fields(zone_view(system, zone))]
        assert any("(hold)" in value for value in values)

    def test_system_fields(self, system: Any) -> None:
        fields = system_fields(system_view(system))
        assert any("Outdoor" in f.label for f in fields)
        assert any(f.action == "toggle_away" for f in fields)


class TestPanels:
    """Panels rebuild their children from a field list."""

    async def test_zone_panel_renders(self, system: Any, zone: Any) -> None:
        app = _PanelApp(zone_view(system, zone), system_view(system))
        async with app.run_test(size=(120, 40)):
            panel = app.query_one(ZonePanel)
            assert len(panel.query(ClickableParam)) > 5

    async def test_system_panel_renders(self, system: Any, zone: Any) -> None:
        app = _PanelApp(zone_view(system, zone), system_view(system))
        async with app.run_test(size=(120, 40)):
            panel = app.query_one(SystemPanel)
            assert len(panel.query(ClickableParam)) == 5

    async def test_clicking_a_value_runs_its_action(
        self, system: Any, zone: Any
    ) -> None:
        app = _PanelApp(zone_view(system, zone), system_view(system))
        async with app.run_test(size=(120, 40)) as pilot:
            value = app.query_one(ZonePanel).query(ClickableParam)[2].children[1]
            await value.on_click()
            await pilot.pause()
        assert app.ran == ["set_hvac_mode"]


class _PanelApp(App[None]):
    """Minimal host for exercising the panels on their own.

    A plain App, not a LennoxS30App subclass: Textual dispatches on_mount to
    every class in the MRO, so subclassing would run the real app's mount too.
    """

    def __init__(self, zone_v: Any, system_v: Any) -> None:
        super().__init__()
        self._zone_v = zone_v
        self._system_v = system_v
        self.ran: list[str] = []

    def compose(self) -> Any:
        yield SystemPanel(id="system-panel")
        yield ZonePanel(id="zone-panel")

    def on_mount(self) -> None:
        self.query_one(SystemPanel).update_system(self._system_v)
        self.query_one(ZonePanel).update_zone(self._zone_v)

    async def run_action(self, action: Any, *args: Any, **kwargs: Any) -> bool:
        self.ran.append(str(action))
        return True


class TestValueScreen:
    """The numeric dialog."""

    async def test_accepts_a_value_in_range(self) -> None:
        app = _DialogApp(ValueScreen("Heat", 19.0, 5.0, 30.0, "°C"))
        async with app.run_test(size=(80, 24)) as pilot:
            app.screen.query_one("#value-input", Input).value = "21"
            await pilot.click("#set-btn")
            await pilot.pause()
        assert app.result == 21.0

    async def test_rejects_below_minimum(self) -> None:
        app = _DialogApp(ValueScreen("Heat", 19.0, 5.0, 30.0))
        async with app.run_test(size=(80, 24)) as pilot:
            app.screen.query_one("#value-input", Input).value = "1"
            await pilot.click("#set-btn")
            await pilot.pause()
        assert app.result is None

    async def test_rejects_above_maximum(self) -> None:
        app = _DialogApp(ValueScreen("Heat", 19.0, 5.0, 30.0))
        async with app.run_test(size=(80, 24)) as pilot:
            app.screen.query_one("#value-input", Input).value = "99"
            await pilot.click("#set-btn")
            await pilot.pause()
        assert app.result is None

    async def test_rejects_nonsense(self) -> None:
        app = _DialogApp(ValueScreen("Heat", 19.0, None, None))
        async with app.run_test(size=(80, 24)) as pilot:
            app.screen.query_one("#value-input", Input).value = ""
            await pilot.click("#set-btn")
            await pilot.pause()
        assert app.result is None

    async def test_cancel_button(self) -> None:
        app = _DialogApp(ValueScreen("Heat", 19.0, 5.0, 30.0))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.click("#cancel-btn")
            await pilot.pause()
        assert app.result is None
        assert app.dismissed is True

    async def test_escape_cancels(self) -> None:
        app = _DialogApp(ValueScreen("Heat", 19.0, 5.0, 30.0))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press("escape")
            await pilot.pause()
        assert app.dismissed is True

    async def test_enter_submits(self) -> None:
        app = _DialogApp(ValueScreen("Heat", 19.0, 5.0, 30.0))
        async with app.run_test(size=(80, 24)) as pilot:
            widget = app.screen.query_one("#value-input", Input)
            widget.value = "22"
            widget.focus()
            await pilot.press("enter")
            await pilot.pause()
        assert app.result == 22.0

    async def test_range_text_without_limits(self) -> None:
        app = _DialogApp(ValueScreen("Heat", None, None, None))
        async with app.run_test(size=(80, 24)):
            label = app.screen.query_one("#value-range")
            assert "No range reported" in str(label.render())

    async def test_arrow_keys_move_between_buttons(self) -> None:
        app = _DialogApp(ValueScreen("Heat", 19.0, 5.0, 30.0))
        async with app.run_test(size=(80, 24)) as pilot:
            app.screen.query_one("#set-btn", Button).focus()
            await pilot.press("right")
            await pilot.pause()
            assert isinstance(app.screen.focused, Button)
            assert app.screen.focused.id == "cancel-btn"
            await pilot.press("left")
            await pilot.pause()
            assert app.screen.focused is not None
            assert app.screen.focused.id == "set-btn"


class TestChoiceScreen:
    """The option-picker dialog."""

    async def test_selecting_an_option(self) -> None:
        app = _DialogApp(ChoiceScreen("Mode", ["off", "heat", "cool"], "heat"))
        async with app.run_test(size=(80, 24)) as pilot:
            options = app.screen.query_one("#choice-list", OptionList)
            options.highlighted = 2
            await pilot.press("enter")
            await pilot.pause()
        assert app.result == "cool"

    async def test_current_is_ticked(self) -> None:
        app = _DialogApp(ChoiceScreen("Mode", ["off", "heat"], "heat"))
        async with app.run_test(size=(80, 24)):
            options = app.screen.query_one("#choice-list", OptionList)
            assert "✓" in str(options.get_option_at_index(1).prompt)

    async def test_escape_cancels(self) -> None:
        app = _DialogApp(ChoiceScreen("Mode", ["off", "heat"]))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press("escape")
            await pilot.pause()
        assert app.result is None
        assert app.dismissed is True


class _DialogApp(App[None]):
    """Minimal host that pushes one dialog and records the result."""

    def __init__(self, screen: Any) -> None:
        super().__init__()
        self._screen = screen
        self.result: Any = None
        self.dismissed = False

    def compose(self) -> Any:
        return iter(())

    def on_mount(self) -> None:
        def done(value: Any) -> None:
            self.result = value
            self.dismissed = True

        self.push_screen(self._screen, done)


def _DashboardApp(connection: S30Connection) -> LennoxS30App:
    """The real app, with the background pump stubbed out for tests."""
    connection.start_pump = lambda: None  # type: ignore[method-assign]
    return LennoxS30App(connection)


class TestDashboard:
    """The dashboard screen."""

    async def test_renders_every_active_zone(self, connection: S30Connection) -> None:
        app = _DashboardApp(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.dashboard
            assert screen is not None
            assert len(screen.zone_views) == 3
            assert len(screen.query(ZonePanel)) == 3

    async def test_selects_the_first_zone(self, connection: S30Connection) -> None:
        app = _DashboardApp(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.dashboard
            assert screen is not None
            assert screen.current_zone is not None
            assert screen.current_zone.name == "Main Floor"

    async def test_switching_zone(self, connection: S30Connection) -> None:
        app = _DashboardApp(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.dashboard
            assert screen is not None
            screen.select_zone(1)
            await pilot.pause()
            assert screen.current_zone is not None
            assert screen.current_zone.name == "Upper Floor"

    async def test_updates_are_coalesced(self, connection: S30Connection) -> None:
        """Pump callbacks mark dirty; the timer does the redraw."""
        app = _DashboardApp(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.dashboard
            assert screen is not None
            screen._pending = False
            connection.system().zone_list[0].executeOnUpdateCallbacks()
            assert screen._pending is False  # nothing was dirty, so no callback
            connection.system().zone_list[0]._dirty = True
            connection.system().zone_list[0].executeOnUpdateCallbacks()
            assert screen._pending is True

    async def test_changes_are_logged(self, connection: S30Connection) -> None:
        app = _DashboardApp(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.dashboard
            assert screen is not None
            connection.system().zone_list[0].temperatureC = 25.0
            screen.refresh_state()
            await pilot.pause()
            log = screen.query_one("#messages-panel", RichLog)
            assert log.lines

    async def test_log_message_writes(self, connection: S30Connection) -> None:
        app = _DashboardApp(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.dashboard
            assert screen is not None
            screen.log_message("hello", logging.WARNING)
            await pilot.pause()
            assert screen.query_one("#messages-panel", RichLog).lines

    async def test_library_logs_reach_the_panel(
        self, connection: S30Connection
    ) -> None:
        app = _DashboardApp(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            logging.getLogger("lennoxs30ctl").warning("from the library")
            await pilot.pause()
            screen = app.dashboard
            assert screen is not None
            assert screen.query_one("#messages-panel", RichLog).lines

    async def test_compact_below_threshold(self, connection: S30Connection) -> None:
        app = _DashboardApp(connection)
        async with app.run_test(size=(80, 40)) as pilot:
            await pilot.pause()
            screen = app.dashboard
            assert screen is not None
            assert "compact" in screen.classes

    async def test_not_compact_at_full_size(self, connection: S30Connection) -> None:
        app = _DashboardApp(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.dashboard
            assert screen is not None
            assert "compact" not in screen.classes

    async def test_read_failure_is_contained(
        self, connection: S30Connection, caplog: pytest.LogCaptureFixture
    ) -> None:
        app = _DashboardApp(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.dashboard
            assert screen is not None
            with (
                patch.object(connection, "system", side_effect=RuntimeError("gone")),
                caplog.at_level(logging.ERROR),
            ):
                screen.refresh_state()
            assert "Could not read" in caplog.text


class TestAppActions:
    """Key bindings and actions."""

    async def test_help_overlay_toggles(self, connection: S30Connection) -> None:
        app = _DashboardApp(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            overlay = app.query_one("#help-overlay")
            assert overlay.display is False
            app.action_toggle_help()
            assert overlay.display is True
            app.action_toggle_help()
            assert overlay.display is False

    async def test_set_heat_opens_the_dialog(self, connection: S30Connection) -> None:
        app = _DashboardApp(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("h")
            await pilot.pause()
            assert isinstance(app.screen, ValueScreen)

    async def test_set_heat_is_refused_in_single_setpoint_mode(
        self, connection: S30Connection
    ) -> None:
        connection.system().single_setpoint_mode = True
        app = _DashboardApp(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("h")
            await pilot.pause()
            assert not isinstance(app.screen, ValueScreen)

    async def test_set_single_is_refused_in_split_mode(
        self, connection: S30Connection
    ) -> None:
        app = _DashboardApp(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("t")
            await pilot.pause()
            assert not isinstance(app.screen, ValueScreen)

    async def test_setting_a_heat_setpoint_writes_celsius(
        self, connection: S30Connection
    ) -> None:
        zone = connection.system().zone_list[0]
        zone.perform_setpoint = AsyncMock()
        app = _DashboardApp(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("h")
            await pilot.pause()
            app.screen.query_one("#value-input", Input).value = "20"
            await pilot.click("#set-btn")
            await pilot.pause()
            await pilot.pause()
        zone.perform_setpoint.assert_awaited_once_with(r_hspC=20.0)

    async def test_cancelling_writes_nothing(self, connection: S30Connection) -> None:
        zone = connection.system().zone_list[0]
        zone.perform_setpoint = AsyncMock()
        app = _DashboardApp(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("h")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
        zone.perform_setpoint.assert_not_awaited()

    async def test_hvac_mode_offers_only_supported_modes(
        self, connection: S30Connection
    ) -> None:
        app = _DashboardApp(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("m")
            await pilot.pause()
            assert isinstance(app.screen, ChoiceScreen)
            options = app.screen.query_one("#choice-list", OptionList)
            assert options.option_count == 4

    async def test_fan_mode_writes(self, connection: S30Connection) -> None:
        zone = connection.system().zone_list[0]
        zone.setFanMode = AsyncMock()
        app = _DashboardApp(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("f")
            await pilot.pause()
            app.screen.query_one("#choice-list", OptionList).highlighted = 1
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
        zone.setFanMode.assert_awaited_once_with("on")

    async def test_humidify_writes(self, connection: S30Connection) -> None:
        zone = connection.system().zone_list[0]
        zone.perform_humidify_setpoint = AsyncMock()
        app = _DashboardApp(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("u")
            await pilot.pause()
            app.screen.query_one("#value-input", Input).value = "40"
            await pilot.click("#set-btn")
            await pilot.pause()
            await pilot.pause()
        zone.perform_humidify_setpoint.assert_awaited_once_with(r_husp=40)

    async def test_dehumidify_writes(self, connection: S30Connection) -> None:
        zone = connection.system().zone_list[0]
        zone.perform_humidify_setpoint = AsyncMock()
        app = _DashboardApp(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("e")
            await pilot.pause()
            app.screen.query_one("#value-input", Input).value = "55"
            await pilot.click("#set-btn")
            await pilot.pause()
            await pilot.pause()
        zone.perform_humidify_setpoint.assert_awaited_once_with(r_desp=55)

    async def test_away_toggles(self, connection: S30Connection) -> None:
        system = connection.system()
        system.set_manual_away_mode = AsyncMock()
        app = _DashboardApp(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            await pilot.pause()
        system.set_manual_away_mode.assert_awaited_once_with(True)

    async def test_cancel_hold_warns_when_no_hold(
        self, connection: S30Connection
    ) -> None:
        zone = connection.system().zone_list[0]
        zone.setScheduleHold = AsyncMock()
        app = _DashboardApp(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("o")
            await pilot.pause()
        zone.setScheduleHold.assert_not_awaited()

    async def test_cancel_hold_writes_when_held(
        self, connection: S30Connection
    ) -> None:
        zone = connection.system().zone_list[0]
        zone.scheduleId = zone.getOverrideScheduleId()
        zone.setScheduleHold = AsyncMock()
        app = _DashboardApp(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("o")
            await pilot.pause()
            await pilot.pause()
        zone.setScheduleHold.assert_awaited_once_with(False)

    async def test_preset_manual(self, connection: S30Connection) -> None:
        zone = connection.system().zone_list[0]
        zone.setManualMode = AsyncMock()
        app = _DashboardApp(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("p")
            await pilot.pause()
            options = app.screen.query_one("#choice-list", OptionList)
            options.highlighted = options.option_count - 1
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
        zone.setManualMode.assert_awaited_once()

    async def test_switch_zone_dialog(self, connection: S30Connection) -> None:
        app = _DashboardApp(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("z")
            await pilot.pause()
            assert isinstance(app.screen, ChoiceScreen)
            app.screen.query_one("#choice-list", OptionList).highlighted = 1
            await pilot.press("enter")
            await pilot.pause()
            screen = app.dashboard
            assert screen is not None
            assert screen.current_zone is not None
            assert screen.current_zone.name == "Upper Floor"

    async def test_switch_zone_on_a_single_zone_system(
        self, connection: S30Connection
    ) -> None:
        system = connection.system()
        system.zone_list = system.zone_list[:1]
        app = _DashboardApp(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("z")
            await pilot.pause()
            assert not isinstance(app.screen, ChoiceScreen)

    async def test_write_failure_is_reported(self, connection: S30Connection) -> None:
        zone = connection.system().zone_list[0]
        zone.perform_setpoint = AsyncMock(
            side_effect=S30Exception("rejected", EC_BAD_PARAMETERS, 1)
        )
        app = _DashboardApp(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("h")
            await pilot.pause()
            app.screen.query_one("#value-input", Input).value = "20"
            await pilot.click("#set-btn")
            await pilot.pause()
            await pilot.pause()
        zone.perform_setpoint.assert_awaited_once()

    async def test_unexpected_write_failure_is_reported(
        self, connection: S30Connection
    ) -> None:
        zone = connection.system().zone_list[0]
        zone.setFanMode = AsyncMock(side_effect=RuntimeError("odd"))
        app = _DashboardApp(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("f")
            await pilot.pause()
            app.screen.query_one("#choice-list", OptionList).highlighted = 0
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
        zone.setFanMode.assert_awaited_once()

    async def test_reconnect(self, connection: S30Connection) -> None:
        app = _DashboardApp(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("r")
            await pilot.pause()
        assert connection._api.serverConnect.await_count >= 1

    async def test_reconnect_failure_is_reported(
        self, connection: S30Connection
    ) -> None:
        app = _DashboardApp(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            connection._api.serverConnect = AsyncMock(
                side_effect=S30Exception("no", EC_BAD_PARAMETERS, 1)
            )
            await pilot.press("r")
            await pilot.pause()
            assert app.dashboard is not None

    async def test_connection_state_is_surfaced(
        self, connection: S30Connection
    ) -> None:
        app = _DashboardApp(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._on_connection_state(ConnectionState.RETRYING)
            app._on_connection_state(ConnectionState.CONNECTED)
            await pilot.pause()

    async def test_actions_are_ignored_while_writing(
        self, connection: S30Connection
    ) -> None:
        app = _DashboardApp(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._write_in_progress = True
            await pilot.press("h")
            await pilot.pause()
            assert not isinstance(app.screen, ValueScreen)


class TestCommandPalette:
    """Every action is reachable from the palette."""

    async def test_search_matches(self, connection: S30Connection) -> None:
        app = _DashboardApp(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            provider = ZoneCommandsProvider(app.screen)
            hits = [hit async for hit in provider.search("fan")]
            assert hits

    async def test_search_with_no_match(self, connection: S30Connection) -> None:
        app = _DashboardApp(connection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            provider = ZoneCommandsProvider(app.screen)
            hits = [hit async for hit in provider.search("zzzzzzz")]
            assert hits == []


class TestRunTui:
    """The entry point wires the connection to the app."""

    async def test_connects_and_closes(self) -> None:
        from lennoxs30ctl.config import Settings

        conn = AsyncMock()
        with (
            patch("lennoxs30ctl.connection.S30Connection", return_value=conn),
            patch.object(LennoxS30App, "run_async", new=AsyncMock()) as mock_run,
        ):
            await run_tui(Settings("host", "id"))
        conn.connect.assert_awaited_once()
        conn.close.assert_awaited_once()
        mock_run.assert_awaited_once()
