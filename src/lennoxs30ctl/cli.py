"""Command-line interface for lennoxs30ctl."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import time
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
from lennoxs30ctl.schedule import (
    EMPTY_SLOTS,
    PERIOD_HEADER,
    WEEKDAYS,
    ScheduleError,
    all_schedules,
    find_schedule,
    format_period,
    period_id,
    running_day,
    schedule_view,
    start_time,
    weekday_index,
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


async def cmd_schedule_list(conn: S30Connection) -> None:
    """List the schedule slots the thermostat has reported."""
    system = conn.system()
    print(f"\n  {'slot':<5} {'name':<18} {'periods':<8} editable")
    print(f"  {_RULE}")
    for view in all_schedules(system):
        editable = "yes" if view.editable else "no"
        print(f"  {view.id:<5} {view.name:<18} {len(view.periods):<8} {editable}")
    empty = f"{EMPTY_SLOTS.start}-{EMPTY_SLOTS.stop - 1}"
    print(f"\n  Slots {empty} are empty until a schedule is created on the panel.\n")


async def cmd_schedule_show(conn: S30Connection, key: str) -> None:
    """Print one schedule as a week."""
    system = conn.system()
    view = schedule_view(system, find_schedule(system, key))
    print(f"\n  [{view.id}] {view.name}  ({len(view.periods)} periods)")
    for day_index, day in enumerate(WEEKDAYS):
        periods = [p for p in view.periods if p.day == day_index]
        if not periods:
            continue
        print(f"\n  {day}")
        print(PERIOD_HEADER)
        for period in periods:
            print(format_period(period))
    print()


async def cmd_schedule_set(
    conn: S30Connection,
    key: str,
    days: list[str],
    period: int,
    values: dict[str, Any],
) -> None:
    """Write one period across one or more days."""
    system = conn.system()
    schedule = find_schedule(system, key)
    unit = system_view(system).unit
    celsius = unit == "C"

    setpoints: dict[str, Any] = {}
    if values.get("heat") is not None:
        setpoints["hspC" if celsius else "hsp"] = values["heat"]
    if values.get("cool") is not None:
        setpoints["cspC" if celsius else "csp"] = values["cool"]
    if values.get("setpoint") is not None:
        setpoints["spC" if celsius else "sp"] = values["setpoint"]

    when = values.get("start_time")
    for day in days:
        day_index = weekday_index(day)
        await system.set_schedule_period(
            schedule.id,
            period_id(day_index, period),
            startTime=None if when is None else start_time(day_index, when),
            enabled=values.get("enabled"),
            fanMode=values.get("fan_mode"),
            systemMode=values.get("system_mode"),
            humidityMode=values.get("humidity_mode"),
            husp=values.get("humidify"),
            desp=values.get("dehumidify"),
            **setpoints,
        )
        print(f"  wrote {day} period {period} of {schedule.name}")

    await conn.drain(_CONFIRM_SECONDS)
    await cmd_schedule_show(conn, key)


async def cmd_schedule_rename(conn: S30Connection, key: str, name: str) -> None:
    """Rename a schedule slot."""
    system = conn.system()
    schedule = find_schedule(system, key)
    previous = schedule.name
    await system.set_schedule_name(schedule.id, name)
    await conn.drain(_CONFIRM_SECONDS)
    print(f"  [{schedule.id}] {previous} -> {schedule.name}")


async def cmd_schedule_whatday(conn: S30Connection) -> None:
    """Check the day 0 mapping against the thermostat's own idea of today."""
    system = conn.system()
    running = running_day(system)
    if running is None:
        print(
            "\n  No zone is running a schedule, so the thermostat is not reporting"
            "\n  a weekday. Put a zone on a schedule preset and try again.\n"
        )
        return
    index, zone_name = running
    print(f"\n  Zone [{zone_name}] is running a period on day index {index}.")
    print(f"  This tool maps day {index} to {WEEKDAYS[index]}.")
    print(f"\n  If today is not {WEEKDAYS[index]}, the mapping is wrong - please")
    print("  open an issue saying what day it actually is.\n")


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

    sp_schedule = subparsers.add_parser("schedule", help="Browse and edit schedules")
    schedule_subs = sp_schedule.add_subparsers(dest="schedule_command", required=True)

    schedule_subs.add_parser("list", help="List the schedule slots")

    sp_show = schedule_subs.add_parser("show", help="Show a schedule as a week")
    sp_show.add_argument("schedule", help="Schedule name or slot number")

    sp_sched_set = schedule_subs.add_parser("set", help="Edit one period")
    sp_sched_set.add_argument("schedule", help="Schedule name or slot number")
    sp_sched_set.add_argument(
        "--days",
        required=True,
        help=f"Comma separated, or 'all'. One of: {', '.join(WEEKDAYS)}",
    )
    sp_sched_set.add_argument(
        "--period",
        required=True,
        type=int,
        choices=range(4),
        help="Which of the four periods in the day",
    )
    sp_sched_set.add_argument("--start", help="Start time as HH:MM")
    sp_sched_set.add_argument(
        "--enabled",
        choices=("on", "off"),
        help="Turning a period off is how the thermostat deletes it",
    )
    sp_sched_set.add_argument("--heat", type=float, help="Heat setpoint")
    sp_sched_set.add_argument("--cool", type=float, help="Cool setpoint")
    sp_sched_set.add_argument("--setpoint", type=float, help="Single setpoint systems")
    sp_sched_set.add_argument("--humidify", type=int, help="Humidify setpoint")
    sp_sched_set.add_argument("--dehumidify", type=int, help="Dehumidify setpoint")
    sp_sched_set.add_argument("--fan-mode", choices=("auto", "on", "circulate"))
    sp_sched_set.add_argument("--system-mode", help="off, cool, heat, heat and cool")
    sp_sched_set.add_argument("--humidity-mode", help="off, humidify, dehumidify, both")

    sp_rename = schedule_subs.add_parser("rename", help="Rename a schedule")
    sp_rename.add_argument("schedule", help="Schedule name or slot number")
    sp_rename.add_argument("name", help="The new name")

    schedule_subs.add_parser(
        "whatday",
        help="Check the day 0 mapping against the thermostat",
    )

    subparsers.add_parser("tui", help="Launch the interactive TUI")

    return parser


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def _parse_days(value: str) -> list[str]:
    """Turn the --days argument into a list of weekday names."""
    if value.strip().casefold() == "all":
        return list(WEEKDAYS)
    days = [part.strip().casefold() for part in value.split(",") if part.strip()]
    if not days:
        msg = "No days given"
        raise ValueError(msg)
    for day in days:
        weekday_index(day)
    return days


def _parse_time(value: str) -> time:
    """Parse an HH:MM start time."""
    try:
        hours, minutes = (int(part) for part in value.split(":", 1))
        return time(hours, minutes)
    except ValueError as exc:
        msg = f"Expected a time as HH:MM, got [{value}]"
        raise ValueError(msg) from exc


async def _dispatch_schedule(conn: S30Connection, args: argparse.Namespace) -> None:
    """Route the schedule subcommands."""
    if args.schedule_command == "list":
        await cmd_schedule_list(conn)
    elif args.schedule_command == "show":
        await cmd_schedule_show(conn, args.schedule)
    elif args.schedule_command == "rename":
        await cmd_schedule_rename(conn, args.schedule, args.name)
    elif args.schedule_command == "whatday":
        await cmd_schedule_whatday(conn)
    elif args.schedule_command == "set":
        enabled = None if args.enabled is None else args.enabled == "on"
        await cmd_schedule_set(
            conn,
            args.schedule,
            _parse_days(args.days),
            args.period,
            {
                "start_time": None if args.start is None else _parse_time(args.start),
                "enabled": enabled,
                "heat": args.heat,
                "cool": args.cool,
                "setpoint": args.setpoint,
                "humidify": args.humidify,
                "dehumidify": args.dehumidify,
                "fan_mode": args.fan_mode,
                "system_mode": args.system_mode,
                "humidity_mode": args.humidity_mode,
            },
        )


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
        elif args.command == "schedule":
            await _dispatch_schedule(conn, args)
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
    except (ConfigError, ConnectionError_, ScheduleError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    except S30Exception as exc:
        print(f"error: {exc.as_string()}", file=sys.stderr)
        sys.exit(1)
