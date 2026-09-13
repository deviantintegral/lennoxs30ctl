"""Screens for the lennoxs30ctl TUI."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from textual.containers import Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, RichLog, Static

from lennoxs30ctl.format import (
    SystemView,
    ZoneView,
    active_zone_views,
    system_view,
)
from lennoxs30ctl.tui.widgets import SystemPanel, ZonePanel

if TYPE_CHECKING:
    from textual import events
    from textual.app import ComposeResult

    from lennoxs30ctl.connection import S30Connection

_LOGGER = logging.getLogger(__name__)

_LEVEL_MARKUP: dict[int, tuple[str, str]] = {
    logging.DEBUG: ("[dim]", "[/dim]"),
    logging.INFO: ("", ""),
    logging.WARNING: ("[yellow]", "[/yellow]"),
    logging.ERROR: ("[red]", "[/red]"),
    logging.CRITICAL: ("[bold red]", "[/bold red]"),
}

#: How often pending updates from the message pump are painted. The pump can
#: deliver a burst of messages, and coalescing them keeps the redraw cheap.
_REDRAW_INTERVAL = 0.25

_COMPACT_THRESHOLD_WIDTH = 100
_COMPACT_THRESHOLD_HEIGHT = 30

_DASHBOARD_CSS = """
#dashboard-container {
    padding: 1 2;
}
#dashboard-container.compact {
    padding: 0 1;
}
#system-panel {
    height: auto;
    width: 1fr;
    padding: 1 2;
    border: solid $primary;
}
#system-panel.compact {
    border: none;
    padding: 0 1;
}
#zone-scroll {
    height: 1fr;
    width: 1fr;
}
ZonePanel {
    height: auto;
    width: 1fr;
    padding: 1 2;
    margin-top: 1;
    border: solid $secondary;
}
ZonePanel.selected {
    border: solid $accent;
}
ZonePanel.compact {
    border: none;
    padding: 0 1;
    margin-top: 0;
}
#messages-label {
    height: auto;
    margin-top: 1;
    padding: 0 2;
    text-style: bold;
}
#messages-label.compact {
    margin-top: 0;
}
#messages-panel {
    height: 8;
    min-height: 4;
    padding: 0 2;
    border: solid $accent;
}
#messages-panel.compact {
    border: none;
    height: 4;
    min-height: 2;
}
"""


class _TuiLogHandler(logging.Handler):
    """Logging handler that writes records into a Textual RichLog widget."""

    def __init__(self, rich_log: RichLog) -> None:
        super().__init__()
        self._rich_log = rich_log

    def emit(self, record: logging.LogRecord) -> None:
        """Write one record, with colour by level."""
        try:
            ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
            msg = self.format(record)
            open_tag, close_tag = _LEVEL_MARKUP.get(record.levelno, ("", ""))
            self._rich_log.write(
                f"[dim]{ts}[/dim] {open_tag}{msg}{close_tag}",
                shrink=False,
            )
        except Exception:  # noqa: BLE001
            self.handleError(record)


class DashboardScreen(Screen[None]):
    """Live dashboard for one system and its zones.

    State is pushed here by the message pump rather than polled. Update
    callbacks only mark the screen dirty; the repaint happens on a timer so a
    burst of messages costs one redraw.
    """

    CSS = _DASHBOARD_CSS

    def __init__(self, connection: S30Connection, name: str | None = None) -> None:
        super().__init__(name=name)
        self.connection = connection
        self.selected_zone: int | None = None
        self._pending = True
        self._log_handler: _TuiLogHandler | None = None
        self._zone_views: list[ZoneView] = []
        self._system_view: SystemView | None = None
        self._previous_zones: dict[int, ZoneView] = {}

    # -- composition ------------------------------------------------------

    def compose(self) -> ComposeResult:
        """Compose the dashboard layout."""
        yield Header()
        with Vertical(id="dashboard-container"):
            yield SystemPanel(id="system-panel")
            yield VerticalScroll(id="zone-scroll")
            yield Static("[bold]Messages[/bold]", id="messages-label")
            yield RichLog(id="messages-panel", markup=True, wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        """Install the log handler, register for updates, start the repaint timer."""
        rich_log = self.query_one("#messages-panel", RichLog)
        self._log_handler = _TuiLogHandler(rich_log)
        self._log_handler.setFormatter(logging.Formatter("%(message)s"))
        logging.getLogger("lennoxs30ctl").addHandler(self._log_handler)

        self._register_callbacks()
        self.set_interval(_REDRAW_INTERVAL, self._apply_pending)
        self.call_after_refresh(self._apply_compact_mode)
        self.call_after_refresh(self.refresh_state)

    def on_unmount(self) -> None:
        """Remove the log handler."""
        if self._log_handler is not None:
            logging.getLogger("lennoxs30ctl").removeHandler(self._log_handler)
            self._log_handler = None

    def _register_callbacks(self) -> None:
        """Ask the library to tell us when anything changes.

        These fire from inside the pump task, so they must do nothing but set a
        flag. The repaint happens on the timer.
        """
        for system in self.connection.systems:
            system.registerOnUpdateCallback(self._mark_dirty)
            for zone in system.zone_list:
                zone.registerOnUpdateCallback(self._mark_dirty)

    def _mark_dirty(self) -> None:
        """Record that something changed; the timer will repaint."""
        self._pending = True

    def _apply_pending(self) -> None:
        """Repaint if anything has changed since the last tick."""
        if not self._pending:
            return
        self._pending = False
        self.refresh_state()

    # -- rendering --------------------------------------------------------

    def refresh_state(self) -> None:
        """Rebuild the panels from the library's current state."""
        try:
            system = self.connection.system()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("Could not read system state: %s", exc)
            return

        self._system_view = system_view(system)
        self._zone_views = active_zone_views(system)

        if self.selected_zone is None and self._zone_views:
            self.selected_zone = self._zone_views[0].zone_id

        self.query_one("#system-panel", SystemPanel).update_system(self._system_view)
        self._sync_zone_panels()
        self._log_changes()

        parts = [self._system_view.name, f"Updated: {datetime.now():%H:%M:%S}"]
        self.sub_title = " | ".join(parts)

    def _sync_zone_panels(self) -> None:
        """Mount one panel per active zone and refresh each."""
        scroll = self.query_one("#zone-scroll", VerticalScroll)
        existing = {panel.id: panel for panel in scroll.query(ZonePanel)}
        compact = "compact" in self.classes

        for view in self._zone_views:
            panel_id = f"zone-{view.zone_id}"
            panel = existing.pop(panel_id, None)
            if panel is None:
                panel = ZonePanel(id=panel_id)
                scroll.mount(panel)
                panel.border_title = view.name
                self.call_after_refresh(panel.update_zone, view)
            else:
                panel.border_title = view.name
                panel.update_zone(view)
            panel.set_class(view.zone_id == self.selected_zone, "selected")
            panel.set_class(compact, "compact")

        for orphan in existing.values():
            orphan.remove()

    def _log_changes(self) -> None:
        """Write a line to the messages panel for anything that moved."""
        for view in self._zone_views:
            previous = self._previous_zones.get(view.zone_id)
            if previous is not None and previous != view:
                for field, old, new in _diff(previous, view):
                    self.log_message(f"[bold]{view.name}[/bold] {field}: {old} → {new}")
            self._previous_zones[view.zone_id] = view

    def log_message(self, msg: str, level: int = logging.INFO) -> None:
        """Write a timestamped, level-coloured line to the messages panel."""
        ts = datetime.now().strftime("%H:%M:%S")
        open_tag, close_tag = _LEVEL_MARKUP.get(level, ("", ""))
        rich_log = self.query_one("#messages-panel", RichLog)
        rich_log.write(f"[dim]{ts}[/dim] {open_tag}{msg}{close_tag}", shrink=False)

    # -- selection --------------------------------------------------------

    @property
    def zone_views(self) -> list[ZoneView]:
        """The active zones, as last rendered."""
        return list(self._zone_views)

    @property
    def current_zone(self) -> ZoneView | None:
        """The zone that actions apply to."""
        for view in self._zone_views:
            if view.zone_id == self.selected_zone:
                return view
        return None

    @property
    def current_system(self) -> SystemView | None:
        """The system, as last rendered."""
        return self._system_view

    def select_zone(self, index: int) -> None:
        """Point the key bindings at a different zone."""
        self.selected_zone = index
        self.refresh_state()

    # -- layout -----------------------------------------------------------

    def _apply_compact_mode(self) -> None:
        """Drop borders and padding on small terminals."""
        width, height = self.app.size.width, self.app.size.height
        compact = width < _COMPACT_THRESHOLD_WIDTH or height < _COMPACT_THRESHOLD_HEIGHT
        self.set_class(compact, "compact")
        for widget_id in (
            "#dashboard-container",
            "#system-panel",
            "#messages-label",
            "#messages-panel",
        ):
            self.query_one(widget_id).set_class(compact, "compact")
        for panel in self.query(ZonePanel):
            panel.set_class(compact, "compact")

    def on_resize(self, event: events.Resize) -> None:
        """Re-evaluate the compact layout."""
        self._apply_compact_mode()


def _diff(old: ZoneView, new: ZoneView) -> list[tuple[str, object, object]]:
    """Return the fields that differ between two zone snapshots."""
    changes: list[tuple[str, object, object]] = []
    for field in ZoneView._fields:
        old_value = getattr(old, field)
        new_value = getattr(new, field)
        if old_value != new_value:
            label = field.replace("_", " ").title()
            changes.append((label, old_value, new_value))
    return changes
