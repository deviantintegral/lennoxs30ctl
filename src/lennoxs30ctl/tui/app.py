"""Main Textual application for the lennoxs30ctl TUI."""

from __future__ import annotations

import logging
from functools import partial
from typing import TYPE_CHECKING, Any, NamedTuple

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.command import Hit, Hits, Provider
from textual.widgets import Footer, Header, Static

from lennoxs30ctl import __version__
from lennoxs30ctl.connection import ConnectionState, S30Exception
from lennoxs30ctl.format import (
    FAN_MODES,
    available_humidity_modes,
    available_hvac_modes,
    preset_names,
)
from lennoxs30ctl.tui.dialogs import ChoiceScreen, ValueScreen
from lennoxs30ctl.tui.screens import DashboardScreen

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from lennoxs30ctl.connection import S30Connection
    from lennoxs30ctl.format import ZoneView

_LOGGER = logging.getLogger("lennoxs30ctl")


class _ControlCommand(NamedTuple):
    """One entry in the command palette."""

    name: str
    help_text: str
    action: str


_CONTROL_COMMANDS: list[_ControlCommand] = [
    _ControlCommand("Heat Setpoint", "Set the heat setpoint", "set_heat"),
    _ControlCommand("Cool Setpoint", "Set the cool setpoint", "set_cool"),
    _ControlCommand("Setpoint", "Set the single setpoint", "set_single"),
    _ControlCommand("HVAC Mode", "Set the hvac mode", "set_hvac_mode"),
    _ControlCommand("Fan Mode", "Set the fan mode", "set_fan_mode"),
    _ControlCommand("Humidity Mode", "Set the humidity mode", "set_humidity_mode"),
    _ControlCommand("Humidify Setpoint", "Set the humidify setpoint", "set_humidify"),
    _ControlCommand(
        "Dehumidify Setpoint", "Set the dehumidify setpoint", "set_dehumidify"
    ),
    _ControlCommand("Schedule", "Select the schedule this zone runs", "set_preset"),
    _ControlCommand("Away", "Toggle manual away mode", "toggle_away"),
    _ControlCommand("Cancel Hold", "Cancel a schedule hold", "cancel_hold"),
    _ControlCommand("Switch Zone", "Point the keys at another zone", "switch_zone"),
    _ControlCommand("Reconnect", "Drop and re-establish the subscription", "reconnect"),
]


class ZoneCommandsProvider(Provider):
    """Command palette provider exposing every thermostat action."""

    async def search(self, query: str) -> Hits:
        """Yield palette hits matching the query."""
        matcher = self.matcher(query)
        for name, help_text, action in _CONTROL_COMMANDS:
            score = matcher.match(name)
            if score > 0:
                yield Hit(
                    score,
                    matcher.highlight(name),
                    partial(self.app.run_action, action),
                    help=help_text,
                )


def _get_zone_commands() -> type[ZoneCommandsProvider]:
    return ZoneCommandsProvider


_APP_CSS = """
#loading-label {
    text-align: center;
    margin: 2 4;
    color: $text-muted;
}
#help-overlay {
    display: none;
}
"""

_HELP_TEXT = """[bold]Keys[/bold]

  h  heat setpoint        m  hvac mode        p  schedule
  c  cool setpoint        f  fan mode         a  toggle away
  t  single setpoint      d  humidity mode    o  cancel hold
  u  humidify setpoint    z  switch zone      r  reconnect
  e  dehumidify setpoint  ?  this help        q  quit

  ctrl+p  command palette

[dim]This client identifies itself to the thermostat with the app_id in
~/.config/lennoxs30ctl/config.toml. It must differ from any other
client's, such as the Home Assistant integration's.[/dim]
"""


class LennoxS30App(App[None]):
    """Textual TUI for monitoring and controlling a Lennox S30."""

    TITLE = f"lennoxs30ctl {__version__}"
    CSS = _APP_CSS
    COMMANDS = App.COMMANDS | {_get_zone_commands}

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "reconnect", "Reconnect"),
        ("question_mark", "toggle_help", "Help"),
        Binding("ctrl+p", "command_palette", "Palette", show=False, priority=True),
        Binding("h", "set_heat", "Heat Setpoint", show=False),
        Binding("c", "set_cool", "Cool Setpoint", show=False),
        Binding("t", "set_single", "Setpoint", show=False),
        Binding("m", "set_hvac_mode", "HVAC Mode", show=False),
        Binding("f", "set_fan_mode", "Fan Mode", show=False),
        Binding("d", "set_humidity_mode", "Humidity Mode", show=False),
        Binding("u", "set_humidify", "Humidify", show=False),
        Binding("e", "set_dehumidify", "Dehumidify", show=False),
        Binding("p", "set_preset", "Schedule", show=False),
        Binding("a", "toggle_away", "Away", show=False),
        Binding("o", "cancel_hold", "Cancel Hold", show=False),
        Binding("z", "switch_zone", "Switch Zone", show=False),
    ]

    def __init__(self, connection: S30Connection) -> None:
        super().__init__()
        self.connection = connection
        self._write_in_progress = False
        self._help_visible = False

    # -- lifecycle --------------------------------------------------------

    def compose(self) -> ComposeResult:
        """Compose the pre-dashboard layout."""
        yield Header()
        yield Static("[bold]Connecting...[/bold]", id="loading-label")
        yield Static(_HELP_TEXT, id="help-overlay")
        yield Footer()

    def on_mount(self) -> None:
        """Show the dashboard and start the message pump."""
        self.connection.add_state_listener(self._on_connection_state)
        self.query_one("#loading-label", Static).remove()
        self.push_screen(DashboardScreen(self.connection))
        self.connection.start_pump()

    def _on_connection_state(self, state: ConnectionState) -> None:
        """Surface connection changes in the log and as a toast."""
        if state == ConnectionState.RETRYING:
            _LOGGER.warning("Connection lost, retrying")
            self.notify("Connection lost, retrying", severity="warning")
        elif state == ConnectionState.CONNECTED:
            _LOGGER.info("Connected")

    # -- helpers ----------------------------------------------------------

    @property
    def dashboard(self) -> DashboardScreen | None:
        """The dashboard screen, when it is the active screen."""
        for screen in self.screen_stack:
            if isinstance(screen, DashboardScreen):
                return screen
        return None

    def _selected(self) -> tuple[DashboardScreen, ZoneView] | None:
        """Return the dashboard and the zone actions apply to, if ready."""
        dashboard = self.dashboard
        if dashboard is None or self._write_in_progress:
            return None
        view = dashboard.current_zone
        if view is None:
            return None
        return dashboard, view

    def _zone_object(self, index: int) -> Any:
        """Look up the library zone object for a zone index."""
        system = self.connection.system()
        return self.connection.zone(system, str(index))

    async def _write(
        self, description: str, call: Callable[[], Awaitable[None]]
    ) -> None:
        """Run a write, reporting failures rather than letting them escape."""
        self._write_in_progress = True
        try:
            await call()
            _LOGGER.info(description)
        except S30Exception as exc:
            _LOGGER.error("%s failed: %s", description, exc.as_string())
            self.notify(exc.message, severity="error", timeout=8)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.exception("%s failed", description)
            self.notify(str(exc), severity="error", timeout=8)
        finally:
            self._write_in_progress = False

    # -- actions ----------------------------------------------------------

    def action_toggle_help(self) -> None:
        """Show or hide the help overlay."""
        overlay = self.query_one("#help-overlay", Static)
        self._help_visible = not self._help_visible
        overlay.display = self._help_visible

    async def action_reconnect(self) -> None:
        """Drop and re-establish the subscription."""
        _LOGGER.info("Reconnecting")
        await self.connection.close()
        try:
            await self.connection.connect()
        except Exception as exc:  # noqa: BLE001
            self.notify(str(exc), severity="error", timeout=8)
            return
        self.connection.start_pump()
        dashboard = self.dashboard
        if dashboard is not None:
            dashboard.refresh_state()

    def _setpoint_action(
        self,
        title: str,
        current: float | None,
        minimum: float | None,
        maximum: float | None,
        kwarg_f: str,
        kwarg_c: str,
    ) -> None:
        """Prompt for a temperature and write it in the thermostat's own unit."""
        selected = self._selected()
        if selected is None:
            return
        _, view = selected
        suffix = f"°{view.unit}"

        def on_result(value: float | None) -> None:
            if value is None:
                return
            keyword = kwarg_c if view.unit == "C" else kwarg_f
            zone = self._zone_object(view.zone_id)
            self.run_worker(
                self._write(
                    f"{view.name} {title.lower()} -> {value:g}{suffix}",
                    lambda: zone.perform_setpoint(**{keyword: value}),
                ),
                exclusive=False,
            )

        self.push_screen(
            ValueScreen(title, current, minimum, maximum, suffix),
            on_result,
        )

    def action_set_heat(self) -> None:
        """Set the heat setpoint."""
        selected = self._selected()
        if selected is None:
            return
        _, view = selected
        if view.single_setpoint_mode:
            self.notify("System is in single setpoint mode; use t", severity="warning")
            return
        points = view.setpoints
        self._setpoint_action(
            "Heat Setpoint",
            points.heat,
            points.min_heat,
            points.max_heat,
            "r_hsp",
            "r_hspC",
        )

    def action_set_cool(self) -> None:
        """Set the cool setpoint."""
        selected = self._selected()
        if selected is None:
            return
        _, view = selected
        if view.single_setpoint_mode:
            self.notify("System is in single setpoint mode; use t", severity="warning")
            return
        points = view.setpoints
        self._setpoint_action(
            "Cool Setpoint",
            points.cool,
            points.min_cool,
            points.max_cool,
            "r_csp",
            "r_cspC",
        )

    def action_set_single(self) -> None:
        """Set the single setpoint."""
        selected = self._selected()
        if selected is None:
            return
        _, view = selected
        if not view.single_setpoint_mode:
            self.notify("System is not in single setpoint mode", severity="warning")
            return
        points = view.setpoints
        self._setpoint_action(
            "Setpoint",
            points.single,
            points.min_heat,
            points.max_cool,
            "r_sp",
            "r_spC",
        )

    def _humidity_action(
        self,
        title: str,
        current: int | None,
        minimum: int | None,
        maximum: int | None,
        keyword: str,
    ) -> None:
        """Prompt for a humidity percentage and write it."""
        selected = self._selected()
        if selected is None:
            return
        _, view = selected

        def on_result(value: float | None) -> None:
            if value is None:
                return
            zone = self._zone_object(view.zone_id)
            self.run_worker(
                self._write(
                    f"{view.name} {title.lower()} -> {int(value)}%",
                    lambda: zone.perform_humidify_setpoint(**{keyword: int(value)}),
                ),
                exclusive=False,
            )

        self.push_screen(
            ValueScreen(title, current, minimum, maximum, "%"),
            on_result,
        )

    def action_set_humidify(self) -> None:
        """Set the humidify setpoint."""
        selected = self._selected()
        if selected is None:
            return
        _, view = selected
        self._humidity_action(
            "Humidify Setpoint",
            view.humidify_setpoint,
            view.min_humidify,
            view.max_humidify,
            "r_husp",
        )

    def action_set_dehumidify(self) -> None:
        """Set the dehumidify setpoint."""
        selected = self._selected()
        if selected is None:
            return
        _, view = selected
        self._humidity_action(
            "Dehumidify Setpoint",
            view.dehumidify_setpoint,
            view.min_dehumidify,
            view.max_dehumidify,
            "r_desp",
        )

    def _choice_action(
        self,
        title: str,
        choices: tuple[str, ...],
        current: str | None,
        method: str,
    ) -> None:
        """Prompt for one of a set of modes and call the matching zone method."""
        selected = self._selected()
        if selected is None:
            return
        _, view = selected

        def on_result(value: str | None) -> None:
            if value is None:
                return
            zone = self._zone_object(view.zone_id)
            self.run_worker(
                self._write(
                    f"{view.name} {title.lower()} -> {value}",
                    lambda: getattr(zone, method)(value),
                ),
                exclusive=False,
            )

        self.push_screen(ChoiceScreen(title, choices, current), on_result)

    def action_set_hvac_mode(self) -> None:
        """Set the hvac mode."""
        selected = self._selected()
        if selected is None:
            return
        _, view = selected
        zone = self._zone_object(view.zone_id)
        self._choice_action(
            "HVAC Mode",
            available_hvac_modes(zone),
            view.hvac_mode,
            "setHVACMode",
        )

    def action_set_fan_mode(self) -> None:
        """Set the fan mode."""
        selected = self._selected()
        if selected is None:
            return
        _, view = selected
        self._choice_action("Fan Mode", FAN_MODES, view.fan_mode, "setFanMode")

    def action_set_humidity_mode(self) -> None:
        """Set the humidity mode."""
        selected = self._selected()
        if selected is None:
            return
        _, view = selected
        zone = self._zone_object(view.zone_id)
        self._choice_action(
            "Humidity Mode",
            available_humidity_modes(zone),
            view.humidity_mode,
            "setHumidityMode",
        )

    def action_set_preset(self) -> None:
        """Select the schedule this zone runs."""
        selected = self._selected()
        if selected is None:
            return
        _, view = selected
        system = self.connection.system()
        choices = (*preset_names(system), "manual")

        def on_result(value: str | None) -> None:
            if value is None:
                return
            zone = self._zone_object(view.zone_id)
            if value == "manual":
                call = zone.setManualMode
                description = f"{view.name} -> manual mode"
            else:

                def call() -> Any:
                    return zone.setSchedule(value)

                description = f"{view.name} schedule -> {value}"
            self.run_worker(self._write(description, call), exclusive=False)

        self.push_screen(ChoiceScreen("Schedule", choices, view.preset), on_result)

    def action_toggle_away(self) -> None:
        """Turn manual away mode on or off."""
        dashboard = self.dashboard
        if dashboard is None or self._write_in_progress:
            return
        system_view = dashboard.current_system
        if system_view is None:
            return
        target = not system_view.manual_away_mode
        system = self.connection.system()
        self.run_worker(
            self._write(
                f"away -> {'on' if target else 'off'}",
                lambda: system.set_manual_away_mode(target),
            ),
            exclusive=False,
        )

    def action_cancel_hold(self) -> None:
        """Cancel a schedule hold on the selected zone."""
        selected = self._selected()
        if selected is None:
            return
        _, view = selected
        if not view.hold_active:
            self.notify("No hold is active on this zone", severity="warning")
            return
        zone = self._zone_object(view.zone_id)
        self.run_worker(
            self._write(
                f"{view.name} hold -> off",
                lambda: zone.setScheduleHold(False),
            ),
            exclusive=False,
        )

    def action_switch_zone(self) -> None:
        """Point the key bindings at a different zone."""
        dashboard = self.dashboard
        if dashboard is None:
            return
        views = dashboard.zone_views
        if len(views) < 2:
            self.notify("This system has a single zone", severity="information")
            return
        names = tuple(view.name for view in views)
        current = dashboard.current_zone

        def on_result(value: str | None) -> None:
            if value is None:
                return
            for view in views:
                if view.name == value:
                    dashboard.select_zone(view.zone_id)
                    return

        self.push_screen(
            ChoiceScreen("Switch Zone", names, current.name if current else None),
            on_result,
        )
