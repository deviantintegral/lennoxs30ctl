"""Widgets for the lennoxs30ctl TUI."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NamedTuple

from textual.containers import Horizontal, Vertical
from textual.widgets import Static

from lennoxs30ctl.format import (
    SystemView,
    ZoneView,
    format_bool,
    format_percent,
    format_setpoints,
    format_temp,
    format_text,
)

if TYPE_CHECKING:
    from textual.app import ComposeResult


class Field(NamedTuple):
    """One label/value pair, optionally wired to an action."""

    label: str
    value: str
    action: str | None = None


class ArrowNavMixin:
    """Mixin for arrow key navigation between buttons in dialog screens."""

    def on_key(self, event: object) -> None:
        """Move focus between buttons with the arrow keys."""
        from textual import events
        from textual.widgets import Button

        if not isinstance(event, events.Key):
            return
        if isinstance(self.focused, Button):  # type: ignore[attr-defined]
            if event.key in ("left", "up"):
                self.focus_previous()  # type: ignore[attr-defined]
                event.prevent_default()
                event.stop()
            elif event.key in ("right", "down"):
                self.focus_next()  # type: ignore[attr-defined]
                event.prevent_default()
                event.stop()


class _ClickableValue(Static):
    """Value portion of a field that can be clicked to run its action."""

    DEFAULT_CSS = """
    _ClickableValue { width: auto; }
    _ClickableValue.clickable { text-style: underline; }
    _ClickableValue.clickable:hover { background: $surface-lighten-2; }
    """

    def __init__(self, content: str, action: str | None = None, **kwargs: Any) -> None:
        super().__init__(content, **kwargs)
        self._action = action
        if action:
            self.add_class("clickable")

    async def on_click(self) -> None:
        """Invoke the associated action when clicked."""
        if self._action:
            await self.app.run_action(self._action)


class ClickableParam(Horizontal):
    """A label with a value that may be clickable into the action that edits it."""

    DEFAULT_CSS = """
    ClickableParam { width: 1fr; height: auto; }
    ClickableParam > .param-label { width: auto; }
    """

    def __init__(
        self,
        label: str,
        value: str,
        action: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._label = label
        self._value = value
        self._action = action

    def compose(self) -> ComposeResult:
        """Compose the label and value children."""
        yield Static(self._label, classes="param-label")
        yield _ClickableValue(self._value, action=self._action)


def system_fields(view: SystemView) -> list[Field]:
    """Build the display fields for a system."""
    alerts = view.active_alerts or 0
    return [
        Field(
            "[bold]Outdoor:[/bold] ", format_temp(view.outdoor_temperature, view.unit)
        ),
        Field("[bold]Away:[/bold] ", format_bool(view.away_mode), "toggle_away"),
        Field("[bold]Zoning:[/bold] ", format_text(view.zoning_mode)),
        Field("[bold]Alert:[/bold] ", f"{format_text(view.alert)} ({alerts} active)"),
        Field("[bold]Software:[/bold] ", format_text(view.software_version)),
    ]


def zone_fields(view: ZoneView) -> list[Field]:
    """Build the display fields for a zone."""
    preset = format_text(view.preset)
    if view.hold_active:
        preset += " (hold)"
    fields = [
        Field("[bold]Temp:[/bold] ", format_temp(view.temperature, view.unit)),
        Field("[bold]Humidity:[/bold] ", format_percent(view.humidity)),
        Field("[bold]Mode:[/bold] ", format_text(view.hvac_mode), "set_hvac_mode"),
        Field("[bold]Fan:[/bold] ", format_text(view.fan_mode), "set_fan_mode"),
    ]
    if view.single_setpoint_mode:
        fields.append(
            Field("[bold]Setpoint:[/bold] ", format_setpoints(view), "set_single")
        )
    else:
        fields.append(
            Field(
                "[bold]Heat:[/bold] ",
                format_temp(view.setpoints.heat, view.unit),
                "set_heat",
            )
        )
        fields.append(
            Field(
                "[bold]Cool:[/bold] ",
                format_temp(view.setpoints.cool, view.unit),
                "set_cool",
            )
        )
    fields.extend(
        [
            Field(
                "[bold]Humidity Mode:[/bold] ",
                format_text(view.humidity_mode),
                "set_humidity_mode",
            ),
            Field(
                "[bold]Humidify:[/bold] ",
                format_percent(view.humidify_setpoint),
                "set_humidify",
            ),
            Field(
                "[bold]Dehumidify:[/bold] ",
                format_percent(view.dehumidify_setpoint),
                "set_dehumidify",
            ),
            Field("[bold]Operation:[/bold] ", format_text(view.temp_operation)),
            Field("[bold]Fan Running:[/bold] ", format_bool(view.fan_running)),
            Field("[bold]Damper:[/bold] ", format_percent(view.damper)),
            Field("[bold]Demand:[/bold] ", format_percent(view.demand)),
            Field("[bold]Schedule:[/bold] ", preset, "set_preset"),
        ]
    )
    return fields


class FieldPanel(Vertical):
    """Container that rebuilds its children from a list of fields."""

    def compose(self) -> ComposeResult:
        """Show a placeholder until the first update arrives."""
        yield Static("[dim]Waiting for the thermostat...[/dim]")

    def update_fields(self, fields: list[Field]) -> None:
        """Replace the panel contents with the given fields."""
        widgets = [ClickableParam(f.label, f.value, action=f.action) for f in fields]
        self.query("*").remove()
        self.mount(*widgets)


class SystemPanel(FieldPanel):
    """Panel showing the system-wide values."""

    def update_system(self, view: SystemView) -> None:
        """Refresh from a system snapshot."""
        self.update_fields(system_fields(view))


class ZonePanel(FieldPanel):
    """Panel showing one zone's values."""

    def update_zone(self, view: ZoneView) -> None:
        """Refresh from a zone snapshot."""
        self.update_fields(zone_fields(view))
