"""Tests for the command-line interface."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest
from lennoxs30api.s30exception import EC_BAD_PARAMETERS, S30Exception

from lennoxs30ctl.cli import (
    _apply_set,
    _parse_bool,
    _parse_number,
    async_main,
    build_parser,
    cmd_list,
    cmd_set,
    cmd_status,
    main,
)

if TYPE_CHECKING:
    from lennoxs30ctl.connection import S30Connection


class TestParser:
    """The argument parser."""

    def test_verbosity_is_exclusive(self) -> None:
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["-v", "--debug", "list"])

    def test_no_command(self) -> None:
        assert build_parser().parse_args([]).command is None

    def test_status_zone_optional(self) -> None:
        assert build_parser().parse_args(["status"]).zone is None
        assert build_parser().parse_args(["status", "0"]).zone == "0"

    def test_set_requires_three_arguments(self) -> None:
        args = build_parser().parse_args(["set", "0", "heat", "19"])
        assert (args.zone, args.param, args.value) == ("0", "heat", "19")

    def test_host_and_app_id(self) -> None:
        args = build_parser().parse_args(["--host", "h", "--app-id", "a", "list"])
        assert args.host == "h"
        assert args.app_id == "a"


class TestListAndStatus:
    """Read-only output."""

    async def test_list(
        self, connection: S30Connection, capsys: pytest.CaptureFixture[str]
    ) -> None:
        await cmd_list(connection)
        out = capsys.readouterr().out
        assert "System (LCC)" in out
        assert "Main Floor" in out
        assert "Upper Floor" in out
        # the inactive zone is not listed
        assert "Zone 4" not in out

    async def test_status_all(
        self, connection: S30Connection, capsys: pytest.CaptureFixture[str]
    ) -> None:
        await cmd_status(connection, None)
        out = capsys.readouterr().out
        assert "Outdoor Temp" in out
        assert "Main Floor" in out
        assert "Basement" in out

    async def test_status_one_zone(
        self, connection: S30Connection, capsys: pytest.CaptureFixture[str]
    ) -> None:
        await cmd_status(connection, "Upper Floor")
        out = capsys.readouterr().out
        assert "Upper Floor" in out
        assert "Outdoor Temp" not in out

    async def test_status_shows_celsius(
        self, connection: S30Connection, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Values come out in the thermostat's own unit."""
        await cmd_status(connection, "0")
        assert "°C" in capsys.readouterr().out

    async def test_status_single_setpoint_mode(
        self, connection: S30Connection, capsys: pytest.CaptureFixture[str]
    ) -> None:
        connection.system().single_setpoint_mode = True
        await cmd_status(connection, "0")
        out = capsys.readouterr().out
        assert "Setpoint:" in out
        assert "Heat / Cool" not in out

    async def test_status_marks_a_hold(
        self, connection: S30Connection, capsys: pytest.CaptureFixture[str]
    ) -> None:
        system = connection.system()
        zone = system.zone_list[0]
        zone.scheduleId = zone.getOverrideScheduleId()
        await cmd_status(connection, "0")
        assert "(hold)" in capsys.readouterr().out


class TestParsers:
    """Argument coercion."""

    @pytest.mark.parametrize("value", ["on", "true", "YES", "1", "enabled"])
    def test_truthy(self, value: str) -> None:
        assert _parse_bool(value) is True

    @pytest.mark.parametrize("value", ["off", "false", "NO", "0", "disabled"])
    def test_falsy(self, value: str) -> None:
        assert _parse_bool(value) is False

    def test_bad_bool(self) -> None:
        with pytest.raises(ValueError, match="Expected on or off"):
            _parse_bool("maybe")

    def test_number(self) -> None:
        assert _parse_number("19.5") == 19.5

    def test_bad_number(self) -> None:
        with pytest.raises(ValueError, match="Expected a number"):
            _parse_number("warm")


class TestApplySet:
    """Each parameter reaches the right library call."""

    async def test_heat_celsius(self, system: Any, zone: Any) -> None:
        zone.perform_setpoint = AsyncMock()
        await _apply_set(system, zone, "C", "heat", "19")
        zone.perform_setpoint.assert_awaited_once_with(r_hspC=19.0)

    async def test_heat_fahrenheit(self, system: Any, zone: Any) -> None:
        zone.perform_setpoint = AsyncMock()
        await _apply_set(system, zone, "F", "heat", "68")
        zone.perform_setpoint.assert_awaited_once_with(r_hsp=68.0)

    async def test_cool_celsius(self, system: Any, zone: Any) -> None:
        zone.perform_setpoint = AsyncMock()
        await _apply_set(system, zone, "C", "cool", "24")
        zone.perform_setpoint.assert_awaited_once_with(r_cspC=24.0)

    async def test_cool_fahrenheit(self, system: Any, zone: Any) -> None:
        zone.perform_setpoint = AsyncMock()
        await _apply_set(system, zone, "F", "cool", "76")
        zone.perform_setpoint.assert_awaited_once_with(r_csp=76.0)

    async def test_single_setpoint(self, system: Any, zone: Any) -> None:
        zone.perform_setpoint = AsyncMock()
        await _apply_set(system, zone, "C", "setpoint", "21")
        zone.perform_setpoint.assert_awaited_once_with(r_spC=21.0)

    async def test_single_setpoint_fahrenheit(self, system: Any, zone: Any) -> None:
        zone.perform_setpoint = AsyncMock()
        await _apply_set(system, zone, "F", "setpoint", "70")
        zone.perform_setpoint.assert_awaited_once_with(r_sp=70.0)

    async def test_hvac_mode(self, system: Any, zone: Any) -> None:
        zone.setHVACMode = AsyncMock()
        await _apply_set(system, zone, "C", "hvac-mode", "heat")
        zone.setHVACMode.assert_awaited_once_with("heat")

    async def test_fan_mode(self, system: Any, zone: Any) -> None:
        zone.setFanMode = AsyncMock()
        await _apply_set(system, zone, "C", "fan-mode", "circulate")
        zone.setFanMode.assert_awaited_once_with("circulate")

    async def test_humidity_mode(self, system: Any, zone: Any) -> None:
        zone.setHumidityMode = AsyncMock()
        await _apply_set(system, zone, "C", "humidity-mode", "off")
        zone.setHumidityMode.assert_awaited_once_with("off")

    async def test_humidify(self, system: Any, zone: Any) -> None:
        zone.perform_humidify_setpoint = AsyncMock()
        await _apply_set(system, zone, "C", "humidify", "40")
        zone.perform_humidify_setpoint.assert_awaited_once_with(r_husp=40)

    async def test_dehumidify(self, system: Any, zone: Any) -> None:
        zone.perform_humidify_setpoint = AsyncMock()
        await _apply_set(system, zone, "C", "dehumidify", "55")
        zone.perform_humidify_setpoint.assert_awaited_once_with(r_desp=55)

    async def test_preset_by_name(self, system: Any, zone: Any) -> None:
        zone.setSchedule = AsyncMock()
        await _apply_set(system, zone, "C", "preset", "summer")
        zone.setSchedule.assert_awaited_once_with("summer")

    async def test_preset_manual(self, system: Any, zone: Any) -> None:
        zone.setManualMode = AsyncMock()
        await _apply_set(system, zone, "C", "preset", "manual")
        zone.setManualMode.assert_awaited_once()

    async def test_hold(self, system: Any, zone: Any) -> None:
        zone.setScheduleHold = AsyncMock()
        await _apply_set(system, zone, "C", "hold", "off")
        zone.setScheduleHold.assert_awaited_once_with(False)

    async def test_away(self, system: Any, zone: Any) -> None:
        system.set_manual_away_mode = AsyncMock()
        await _apply_set(system, zone, "C", "away", "on")
        system.set_manual_away_mode.assert_awaited_once_with(True)

    async def test_unknown_parameter(self, system: Any, zone: Any) -> None:
        with pytest.raises(ValueError, match="Unknown parameter"):
            await _apply_set(system, zone, "C", "vibes", "good")


class TestCmdSet:
    """Setting reads back afterwards."""

    async def test_reads_back_after_writing(
        self, connection: S30Connection, capsys: pytest.CaptureFixture[str]
    ) -> None:
        zone = connection.system().zone_list[0]
        zone.perform_setpoint = AsyncMock()
        await cmd_set(connection, "0", "heat", "19")
        zone.perform_setpoint.assert_awaited_once()
        # drain runs so the printed line is the panel's state
        connection._api.messagePump.assert_awaited()
        assert "Main Floor" in capsys.readouterr().out


class TestMain:
    """The synchronous entry point."""

    def test_config_error_exits_nonzero(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("LENNOXS30_HOST", raising=False)
        monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/definitely-not-here")
        with pytest.raises(SystemExit) as exc:
            main(["list"])
        assert exc.value.code == 1
        assert "No thermostat host" in capsys.readouterr().err

    def test_s30_exception_is_reported(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LENNOXS30_HOST", "thermostat.lan")
        with (
            patch(
                "lennoxs30ctl.cli.async_main",
                side_effect=S30Exception("nope", EC_BAD_PARAMETERS, 1),
            ),
            pytest.raises(SystemExit) as exc,
        ):
            main(["list"])
        assert exc.value.code == 1
        assert "nope" in capsys.readouterr().err

    def test_keyboard_interrupt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LENNOXS30_HOST", "thermostat.lan")
        with (
            patch("lennoxs30ctl.cli.async_main", side_effect=KeyboardInterrupt),
            pytest.raises(SystemExit) as exc,
        ):
            main(["list"])
        assert exc.value.code == 130

    def test_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LENNOXS30_HOST", "thermostat.lan")
        with patch("lennoxs30ctl.cli.async_main", new=AsyncMock()) as mock_main:
            main(["list"])
        mock_main.assert_awaited_once()


class TestAsyncMain:
    """Dispatch to the subcommands."""

    async def test_dispatches_list(
        self, connection: S30Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LENNOXS30_HOST", "thermostat.lan")
        args = build_parser().parse_args(["list"])
        with (
            patch("lennoxs30ctl.cli.S30Connection", return_value=connection),
            patch("lennoxs30ctl.cli.cmd_list", new=AsyncMock()) as mock_list,
        ):
            await async_main(args)
        mock_list.assert_awaited_once()

    async def test_dispatches_status(
        self, connection: S30Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LENNOXS30_HOST", "thermostat.lan")
        args = build_parser().parse_args(["status", "0"])
        with (
            patch("lennoxs30ctl.cli.S30Connection", return_value=connection),
            patch("lennoxs30ctl.cli.cmd_status", new=AsyncMock()) as mock_status,
        ):
            await async_main(args)
        mock_status.assert_awaited_once()

    async def test_dispatches_set(
        self, connection: S30Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LENNOXS30_HOST", "thermostat.lan")
        args = build_parser().parse_args(["set", "0", "heat", "19"])
        with (
            patch("lennoxs30ctl.cli.S30Connection", return_value=connection),
            patch("lennoxs30ctl.cli.cmd_set", new=AsyncMock()) as mock_set,
        ):
            await async_main(args)
        mock_set.assert_awaited_once()

    async def test_no_command_launches_the_tui(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LENNOXS30_HOST", "thermostat.lan")
        args = build_parser().parse_args([])
        with patch("lennoxs30ctl.cli.cmd_tui", new=AsyncMock()) as mock_tui:
            await async_main(args)
        mock_tui.assert_awaited_once()

    async def test_tui_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LENNOXS30_HOST", "thermostat.lan")
        args = build_parser().parse_args(["tui"])
        with patch("lennoxs30ctl.cli.cmd_tui", new=AsyncMock()) as mock_tui:
            await async_main(args)
        mock_tui.assert_awaited_once()
