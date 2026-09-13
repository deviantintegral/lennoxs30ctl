"""Modal dialogs for the lennoxs30ctl TUI.

Two shapes cover everything the thermostat exposes: a number with a range, and
a choice from a list.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets.option_list import Option

from lennoxs30ctl.tui.widgets import ArrowNavMixin

if TYPE_CHECKING:
    from collections.abc import Sequence

    from textual.app import ComposeResult

_VALUE_CSS = """
ValueScreen {
    align: center middle;
}

#value-dialog {
    width: 50;
    height: auto;
    padding: 1 2;
    border: thick $primary;
    background: $surface;
}

#value-title {
    text-align: center;
    text-style: bold;
    margin-bottom: 1;
}

#value-input {
    width: 1fr;
    margin-bottom: 1;
}

#value-range {
    text-align: center;
    margin-bottom: 1;
    color: $text-muted;
}

#value-buttons {
    height: auto;
    align: center middle;
}

#value-buttons Button {
    margin: 0 1;
    min-width: 16;
}
"""

_CHOICE_CSS = """
ChoiceScreen {
    align: center middle;
}

#choice-dialog {
    width: 50;
    height: auto;
    max-height: 80%;
    padding: 1 2;
    border: thick $primary;
    background: $surface;
}

#choice-title {
    text-align: center;
    text-style: bold;
    margin-bottom: 1;
}
"""


class ValueScreen(ArrowNavMixin, ModalScreen[float | None]):
    """Modal for entering a number within a range."""

    CSS = _VALUE_CSS

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        title: str,
        current: float | None,
        minimum: float | None,
        maximum: float | None,
        suffix: str = "",
        name: str | None = None,
    ) -> None:
        super().__init__(name=name)
        self._title = title
        self._current = current
        self._minimum = minimum
        self._maximum = maximum
        self._suffix = suffix

    def _range_text(self) -> str:
        """Describe the accepted range, when the thermostat reported one."""
        if self._minimum is None or self._maximum is None:
            return "No range reported by the thermostat"
        low = f"{self._minimum:g}"
        high = f"{self._maximum:g}"
        return f"Valid range: {low} – {high}{self._suffix}"

    def compose(self) -> ComposeResult:
        """Compose the dialog."""
        current = "" if self._current is None else f"{self._current:g}"
        with Vertical(id="value-dialog"):
            yield Static(
                f"{self._title} (current: {current or '--'})", id="value-title"
            )
            yield Input(value=current, type="number", id="value-input")
            yield Static(self._range_text(), id="value-range")
            with Horizontal(id="value-buttons"):
                yield Button("Set", variant="primary", id="set-btn")
                yield Button("Cancel", variant="default", id="cancel-btn")

    def _validate_and_dismiss(self) -> None:
        """Validate the input and dismiss with the value."""
        widget = self.query_one("#value-input", Input)
        try:
            value = float(widget.value)
        except ValueError:
            self.notify("Please enter a valid number", severity="error")
            return
        if self._minimum is not None and value < self._minimum:
            self.notify(f"Must be at least {self._minimum:g}", severity="error")
            return
        if self._maximum is not None and value > self._maximum:
            self.notify(f"Must be at most {self._maximum:g}", severity="error")
            return
        self.dismiss(value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle the Set and Cancel buttons."""
        if event.button.id == "set-btn":
            self._validate_and_dismiss()
        elif event.button.id == "cancel-btn":
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Treat enter in the input as pressing Set."""
        self._validate_and_dismiss()

    def action_cancel(self) -> None:
        """Dismiss without a value."""
        self.dismiss(None)


class ChoiceScreen(ModalScreen[str | None]):
    """Modal for picking one option from a list."""

    CSS = _CHOICE_CSS

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        title: str,
        choices: Sequence[str],
        current: str | None = None,
        name: str | None = None,
    ) -> None:
        super().__init__(name=name)
        self._title = title
        self._choices = list(choices)
        self._current = current

    def compose(self) -> ComposeResult:
        """Compose the dialog."""
        with Vertical(id="choice-dialog"):
            yield Static(self._title, id="choice-title")
            options = [
                Option(f"{c} ✓" if c == self._current else c, id=c)
                for c in self._choices
            ]
            yield OptionList(*options, id="choice-list")

    def on_option_list_option_selected(
        self,
        event: OptionList.OptionSelected,
    ) -> None:
        """Dismiss with the selected option."""
        self.dismiss(str(event.option.id))

    def action_cancel(self) -> None:
        """Dismiss without a choice."""
        self.dismiss(None)
