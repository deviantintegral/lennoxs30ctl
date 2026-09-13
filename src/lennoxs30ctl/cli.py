"""Command-line interface for lennoxs30ctl."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import TYPE_CHECKING, Any

from lennoxs30ctl.config import ConfigError, Settings, resolve
from lennoxs30ctl.connection import (
    ConnectionError_,
    S30Connection,
    S30Exception,
)
from lennoxs30ctl.format import (
    SystemView,
    ZoneView,
    active_zone_views,
    format_bool,
    format_percent,
    format_setpoints,
    format_temp,
    format_text,
    system_view,
    zone_view,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

_LOGGER = logging.getLogger(__name__)

_RULE = "─" * 44

_SET_PARAMS = (
    "heat, cool, setpoint, hvac-mode, fan-mode, humidity-mode,"
    " humidify, dehumidify, preset, hold, away"
)

#: How long to keep reading messages after a write, so the confirmation we
#: print back is the thermostat's own state rather than what we asked for.
_CONFIRM_SECONDS = 5.0


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------


def _display_system(view: SystemView) -> None:
    """Print the system section."""
    print(f"\n  {view.name} ({view.sys_id})")
    print(f"  {_RULE}")
    print(f"    Outdoor Temp:   {format_temp(view.outdoor_temperature, view.unit)}")
    print(f"    Zones:          {view.number_of_zones or '--'}")
    print(f"    Zoning Mode:    {format_text(view.zoning_mode)}")
    print(f"    Central Mode:   {format_bool(view.central_mode)}")
    print(f"    Away:           {format_bool(view.away_mode)}")
    print(f"    Alert:          {format_text(view.alert)}")
    print(f"    Active Alerts:  {view.active_alerts if view.active_alerts else 0}")
    print(f"    Product:        {format_text(view.product_type)}")
    print(f"    Software:       {format_text(view.software_version)}")
    if view.single_setpoint_mode:
        print("    Setpoint Mode:  single")


def _display_zone(view: ZoneView) -> None:
    """Print one zone section."""
    print(f"\n  [{view.zone_id}] {view.name}")
    print(f"  {_RULE}")
    print(f"    Temperature:    {format_temp(view.temperature, view.unit)}")
    print(f"    Humidity:       {format_percent(view.humidity)}")
    print(f"    HVAC Mode:      {format_text(view.hvac_mode)}")
    print(f"    Fan Mode:       {format_text(view.fan_mode)}")
    if view.single_setpoint_mode:
        print(f"    Setpoint:       {format_setpoints(view)}")
    else:
        print(f"    Heat / Cool:    {format_setpoints(view)}")
    print(f"    Humidity Mode:  {format_text(view.humidity_mode)}")
    print(f"    Humidify:       {format_percent(view.humidify_setpoint)}")
    print(f"    Dehumidify:     {format_percent(view.dehumidify_setpoint)}")
    print(f"    Operation:      {format_text(view.temp_operation)}")
    print(f"    Fan Running:    {format_bool(view.fan_running)}")
    print(f"    Damper:         {format_percent(view.damper)}")
    print(f"    Demand:         {format_percent(view.demand)}")
    print(f"    Allergen Def:   {format_bool(view.allergen_defender)}")
    preset = format_text(view.preset)
    if view.hold_active:
        preset += " (hold)"
    print(f"    Schedule:       {preset}")


def _zone_summary(view: ZoneView) -> str:
    """One line summarising a zone, used by list and by set confirmations."""
    return (
        f"  [{view.zone_id}] {view.name:<16} "
        f"{format_temp(view.temperature, view.unit):>7}  "
        f"{format_percent(view.humidity):>4}  "
        f"{format_text(view.hvac_mode):<14} "
        f"{format_setpoints(view)}"
    )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


async def cmd_list(conn: S30Connection) -> None:
    """List the systems and their active zones."""
    for system in conn.systems:
        view = system_view(system)
        print(f"\n{view.name} ({view.sys_id})")
        print(
            f"  {'idx':<4} {'name':<16} {'temp':>7}  {'hum':>4}  {'mode':<14} setpoints"
        )
        for zone in active_zone_views(system):
            print(_zone_summary(zone))
    print()


async def cmd_status(conn: S30Connection, zone_key: str | None) -> None:
    """Show the full status of one zone, or of everything."""
    system = conn.system()
    if zone_key is not None:
        _display_zone(zone_view(system, conn.zone(system, zone_key)))
        print()
        return
    _display_system(system_view(system))
    for view in active_zone_views(system):
        _display_zone(view)
    print()


def _parse_bool(value: str) -> bool:
    """Parse an on/off style argument."""
    lowered = value.strip().casefold()
    if lowered in ("on", "true", "yes", "1", "enable", "enabled"):
        return True
    if lowered in ("off", "false", "no", "0", "disable", "disabled"):
        return False
    msg = f"Expected on or off, got [{value}]"
    raise ValueError(msg)


def _parse_number(value: str) -> float:
    """Parse a numeric argument."""
    try:
        return float(value)
    except ValueError as exc:
        msg = f"Expected a number, got [{value}]"
        raise ValueError(msg) from exc


async def _apply_set(
    system: Any,
    zone: Any,
    unit: str,
    param: str,
    value: str,
) -> None:
    """Dispatch a single set operation onto the library."""
    celsius = unit == "C"
    if param == "heat":
        number = _parse_number(value)
        if celsius:
            await zone.perform_setpoint(r_hspC=number)
        else:
            await zone.perform_setpoint(r_hsp=number)
    elif param == "cool":
        number = _parse_number(value)
        if celsius:
            await zone.perform_setpoint(r_cspC=number)
        else:
            await zone.perform_setpoint(r_csp=number)
    elif param == "setpoint":
        number = _parse_number(value)
        if celsius:
            await zone.perform_setpoint(r_spC=number)
        else:
            await zone.perform_setpoint(r_sp=number)
    elif param == "hvac-mode":
        await zone.setHVACMode(value)
    elif param == "fan-mode":
        await zone.setFanMode(value)
    elif param == "humidity-mode":
        await zone.setHumidityMode(value)
    elif param == "humidify":
        await zone.perform_humidify_setpoint(r_husp=int(_parse_number(value)))
    elif param == "dehumidify":
        await zone.perform_humidify_setpoint(r_desp=int(_parse_number(value)))
    elif param == "preset":
        if value.casefold() == "manual":
            await zone.setManualMode()
        else:
            await zone.setSchedule(value)
    elif param == "hold":
        await zone.setScheduleHold(_parse_bool(value))
    elif param == "away":
        await system.set_manual_away_mode(_parse_bool(value))
    else:
        msg = f"Unknown parameter [{param}]. Valid parameters: {_SET_PARAMS}"
        raise ValueError(msg)


async def cmd_set(
    conn: S30Connection,
    zone_key: str,
    param: str,
    value: str,
) -> None:
    """Set a parameter on a zone and print what the thermostat reports back."""
    system = conn.system()
    zone = conn.zone(system, zone_key)
    unit = system_view(system).unit

    await _apply_set(system, zone, unit, param, value)

    # Read for a few seconds so the line below is the panel's state, not ours.
    await conn.drain(_CONFIRM_SECONDS)
    print(_zone_summary(zone_view(system, zone)))


async def cmd_tui(settings: Settings, *, log_level: int) -> None:
    """Launch the TUI, showing an install message when the extra is missing."""
    try:
        from lennoxs30ctl.tui import run_tui
    except ImportError:
        print("The TUI requires the 'tui' extra. Run with:")
        print("  uv tool run 'lennoxs30ctl[tui]' tui")
        sys.exit(1)
    await run_tui(settings, log_level=log_level)


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build and return the argparse parser for the CLI."""
    parser = argparse.ArgumentParser(
        prog="lennoxs30ctl",
        description=(
            "Control a Lennox S30/E30/M30 thermostat over the local API."
            " An app_id is generated on first run and saved to the config file;"
            " it identifies this client to the thermostat and must differ from"
            " any other client's, such as the Home Assistant integration's."
        ),
    )
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    verbosity.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose logging including the raw thermostat messages",
    )
    parser.add_argument(
        "--host",
        help="Thermostat hostname or IP (or set LENNOXS30_HOST)",
    )
    parser.add_argument(
        "--app-id",
        help="Endpoint id on the thermostat; must differ from any other client",
    )

    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("list", help="List systems and zones")

    sp_status = subparsers.add_parser("status", help="Show current status")
    sp_status.add_argument("zone", nargs="?", help="Zone index or name")

    sp_set = subparsers.add_parser("set", help="Set a zone parameter")
    sp_set.add_argument("zone", help="Zone index or name")
    sp_set.add_argument("param", help=f"Parameter name: {_SET_PARAMS}")
    sp_set.add_argument("value", help="Value to set")

    subparsers.add_parser("tui", help="Launch the interactive TUI")

    return parser


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def _log_level(args: argparse.Namespace) -> int:
    """Map the verbosity flags onto a logging level."""
    if args.debug:
        return logging.DEBUG
    if args.verbose:
        return logging.INFO
    return logging.WARNING


async def async_main(args: argparse.Namespace) -> None:
    """Run the requested subcommand."""
    settings = resolve(args.host, args.app_id)
    level = _log_level(args)

    if args.command is None or args.command == "tui":
        await cmd_tui(settings, log_level=level)
        return

    conn = S30Connection(
        settings.host,
        settings.app_id,
        message_logging=args.debug,
    )
    async with conn:
        if args.command == "list":
            await cmd_list(conn)
        elif args.command == "status":
            await cmd_status(conn, args.zone)
        elif args.command == "set":
            await cmd_set(conn, args.zone, args.param, args.value)


def main(argv: Sequence[str] | None = None) -> None:
    """Entry point for the lennoxs30ctl CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=_log_level(args))
    try:
        asyncio.run(async_main(args))
    except KeyboardInterrupt:
        sys.exit(130)
    except (ConfigError, ConnectionError_, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    except S30Exception as exc:
        print(f"error: {exc.as_string()}", file=sys.stderr)
        sys.exit(1)
