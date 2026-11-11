"""Typed views over the lennoxs30api objects.

lennoxs30api ships no ``py.typed`` marker, so everything it exports is ``Any``.
This module and :mod:`lennoxs30ctl.connection` are the only ones that touch it.
Everything else renders from the :class:`SystemView` and :class:`ZoneView`
values built here, which are ordinary typed objects.
"""

from __future__ import annotations

import logging
from functools import cache
from typing import Any, Literal, NamedTuple

_LOGGER = logging.getLogger(__name__)

TempUnit = Literal["C", "F"]

#: Fallback when the thermostat has not reported its configured unit yet.
#:
#: Reaching this means labelling real temperatures with a guessed unit, which
#: reads as plausible rather than broken, so :func:`system_unit` says so out
#: loud. It should now be unreachable: temperatureUnit and the system name
#: arrive in the same message, and connect waits for the name.
DEFAULT_UNIT: TempUnit = "F"


def _opt_float(value: Any) -> float | None:
    """Coerce a library value to a float, or None when it is absent."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _opt_int(value: Any) -> int | None:
    """Coerce a library value to an int, or None when it is absent."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _opt_str(value: Any) -> str | None:
    """Coerce a library value to a str, or None when it is absent."""
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _opt_bool(value: Any) -> bool | None:
    """Coerce a library value to a bool, or None when it is absent."""
    if value is None:
        return None
    return bool(value)


#: Fan modes the thermostat accepts. Every system supports all three.
FAN_MODES: tuple[str, ...] = ("auto", "on", "circulate")


class Setpoints(NamedTuple):
    """The setpoints for a zone, already in the thermostat's display unit."""

    heat: float | None
    cool: float | None
    single: float | None
    min_heat: float | None
    max_heat: float | None
    min_cool: float | None
    max_cool: float | None


class ZoneView(NamedTuple):
    """A read-only snapshot of one zone."""

    zone_id: int
    name: str
    active: bool
    unit: TempUnit
    single_setpoint_mode: bool
    temperature: float | None
    humidity: int | None
    hvac_mode: str | None
    fan_mode: str | None
    humidity_mode: str | None
    setpoints: Setpoints
    humidify_setpoint: int | None
    dehumidify_setpoint: int | None
    min_humidify: int | None
    max_humidify: int | None
    min_dehumidify: int | None
    max_dehumidify: int | None
    temp_operation: str | None
    hum_operation: str | None
    fan_running: bool | None
    damper: int | None
    demand: int | None
    allergen_defender: bool | None
    preset: str | None
    hold_active: bool


class SystemView(NamedTuple):
    """A read-only snapshot of one system."""

    sys_id: str
    name: str
    unit: TempUnit
    single_setpoint_mode: bool
    outdoor_temperature: float | None
    number_of_zones: int | None
    zoning_mode: str | None
    central_mode: bool | None
    away_mode: bool
    manual_away_mode: bool
    alert: str | None
    active_alerts: int | None
    software_version: str | None
    product_type: str | None
    schedule_names: tuple[str, ...]


@cache
def _warn_unknown_unit(reported: str | None) -> None:
    """Complain once per distinct unrecognised unit.

    Cached rather than flagged, because system_unit runs on every rendered
    value and a per-value warning would bury the output it is warning about.
    """
    _LOGGER.warning(
        "Thermostat reported temperature unit [%s]; assuming %s. Temperatures "
        "below are real values but may carry the wrong unit label.",
        reported,
        DEFAULT_UNIT,
    )


def system_unit(system: Any) -> TempUnit:
    """Return the unit the thermostat itself is configured to display."""
    unit = _opt_str(system.temperatureUnit)
    if unit is not None and unit.upper().startswith("C"):
        return "C"
    if unit is not None and unit.upper().startswith("F"):
        return "F"
    _warn_unknown_unit(unit)
    return DEFAULT_UNIT


def _zone_setpoints(zone: Any, unit: TempUnit, single_mode: bool) -> Setpoints:
    """Pull the setpoints and their limits in the thermostat's own unit."""
    celsius = unit == "C"
    if single_mode:
        single = _opt_float(zone.spC if celsius else zone.sp)
        heat = cool = None
    else:
        single = None
        heat = _opt_float(zone.hspC if celsius else zone.hsp)
        cool = _opt_float(zone.cspC if celsius else zone.csp)
    return Setpoints(
        heat=heat,
        cool=cool,
        single=single,
        min_heat=_opt_float(zone.minHspC if celsius else zone.minHsp),
        max_heat=_opt_float(zone.maxHspC if celsius else zone.maxHsp),
        min_cool=_opt_float(zone.minCspC if celsius else zone.minCsp),
        max_cool=_opt_float(zone.maxCspC if celsius else zone.maxCsp),
    )


def _zone_preset(system: Any, zone: Any) -> str | None:
    """Return the name of the schedule the zone is running, if any."""
    schedule_id = zone.scheduleId
    if schedule_id is None:
        return None
    schedule = system.getSchedule(schedule_id)
    if schedule is None:
        return None
    return _opt_str(schedule.name)


def zone_view(system: Any, zone: Any) -> ZoneView:
    """Build a typed snapshot of a zone."""
    unit = system_unit(system)
    single_mode = bool(system.single_setpoint_mode)
    celsius = unit == "C"
    return ZoneView(
        zone_id=int(zone.id),
        name=_opt_str(zone.name) or f"Zone {int(zone.id) + 1}",
        active=bool(zone.is_zone_active()),
        unit=unit,
        single_setpoint_mode=single_mode,
        temperature=_opt_float(zone.temperatureC if celsius else zone.temperature),
        humidity=_opt_int(zone.humidity),
        hvac_mode=_opt_str(zone.systemMode),
        fan_mode=_opt_str(zone.fanMode),
        humidity_mode=_opt_str(zone.humidityMode),
        setpoints=_zone_setpoints(zone, unit, single_mode),
        humidify_setpoint=_opt_int(zone.husp),
        dehumidify_setpoint=_opt_int(zone.desp),
        min_humidify=_opt_int(zone.minHumSp),
        max_humidify=_opt_int(zone.maxHumSp),
        min_dehumidify=_opt_int(zone.minDehumSp),
        max_dehumidify=_opt_int(zone.maxDehumSp),
        temp_operation=_opt_str(zone.tempOperation),
        hum_operation=_opt_str(zone.humOperation),
        fan_running=_opt_bool(zone.fan),
        damper=_opt_int(zone.damper),
        demand=_opt_int(zone.demand),
        allergen_defender=_opt_bool(zone.allergenDefender),
        preset=_zone_preset(system, zone),
        hold_active=bool(zone.isZoneOveride()),
    )


def system_view(system: Any) -> SystemView:
    """Build a typed snapshot of a system."""
    unit = system_unit(system)
    celsius = unit == "C"
    outdoor = system.outdoorTemperatureC if celsius else system.outdoorTemperature
    names: list[str] = []
    for schedule in system.getSchedules():
        name = _opt_str(schedule.name)
        if name is not None:
            names.append(name)
    return SystemView(
        sys_id=str(system.sysId),
        name=_opt_str(system.name) or str(system.sysId),
        unit=unit,
        single_setpoint_mode=bool(system.single_setpoint_mode),
        outdoor_temperature=_opt_float(outdoor),
        number_of_zones=_opt_int(system.numberOfZones),
        zoning_mode=_opt_str(system.zoningMode),
        central_mode=_opt_bool(system.centralMode),
        away_mode=bool(system.get_away_mode()),
        manual_away_mode=bool(system.get_manual_away_mode()),
        alert=_opt_str(system.alert),
        active_alerts=_opt_int(system.alerts_num_active),
        software_version=_opt_str(system.softwareVersion),
        product_type=_opt_str(system.productType),
        schedule_names=tuple(names),
    )


def available_hvac_modes(zone: Any) -> tuple[str, ...]:
    """The hvac modes this zone's equipment can actually do.

    Mirrors how the Home Assistant integration builds its mode list from the
    zone's heating and cooling options.
    """
    modes = ["off"]
    if zone.coolingOption:
        modes.append("cool")
    if zone.heatingOption:
        modes.append("heat")
    if zone.coolingOption and zone.heatingOption:
        modes.append("heat and cool")
    if zone.emergencyHeatingOption:
        modes.append("emergency heat")
    return tuple(modes)


def available_humidity_modes(zone: Any) -> tuple[str, ...]:
    """The humidity modes this zone's equipment can actually do."""
    modes = ["off"]
    if zone.dehumidificationOption:
        modes.append("dehumidify")
    if zone.humidificationOption:
        modes.append("humidify")
    if zone.humidificationOption and zone.dehumidificationOption:
        modes.append("both")
    return tuple(modes)


def preset_names(system: Any) -> tuple[str, ...]:
    """Schedule names that can be selected as a preset.

    Slots from 16 up are the thermostat's internal manual, away and hold
    schedules, so they are not offered.
    """
    names: list[str] = []
    for schedule in system.getSchedules():
        if int(schedule.id) >= 16:
            continue
        name = _opt_str(schedule.name)
        if name is not None:
            names.append(name)
    return tuple(names)


def active_zone_views(system: Any) -> list[ZoneView]:
    """Build snapshots for every zone that the thermostat reports as active."""
    views = [zone_view(system, zone) for zone in system.zone_list]
    return [view for view in views if view.active]


# ---------------------------------------------------------------------------
# Display formatting
# ---------------------------------------------------------------------------


def format_temp(value: float | None, unit: TempUnit) -> str:
    """Format a temperature, dropping a trailing .0 so whole degrees read cleanly."""
    if value is None:
        return "--"
    text = f"{value:.1f}".removesuffix(".0")
    return f"{text}°{unit}"


def format_percent(value: int | None) -> str:
    """Format a percentage value."""
    return "--" if value is None else f"{value}%"


def format_bool(value: bool | None) -> str:
    """Format an on/off value."""
    if value is None:
        return "--"
    return "on" if value else "off"


def format_text(value: str | None) -> str:
    """Format a free-text value."""
    return value if value else "--"


def format_setpoints(view: ZoneView) -> str:
    """Format a zone's setpoints as a single line."""
    points = view.setpoints
    if view.single_setpoint_mode:
        return format_temp(points.single, view.unit)
    heat = format_temp(points.heat, view.unit)
    cool = format_temp(points.cool, view.unit)
    return f"{heat} / {cool}"
