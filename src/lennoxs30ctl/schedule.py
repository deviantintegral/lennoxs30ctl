"""Reading and writing thermostat schedules.

A schedule has 28 periods: four a day for seven days, where the period id is
``day * 4 + period`` and the period's ``startTime`` is seconds since the start
of the week rather than since midnight.

Like :mod:`lennoxs30ctl.format`, this module is allowed to touch the untyped
lennoxs30api objects and hands typed values to everything else.
"""

from __future__ import annotations

from datetime import time
from typing import Any, NamedTuple

from lennoxs30ctl.format import TempUnit, format_temp, format_text, system_unit

#: Day 0 is Monday. Nothing in the protocol says so, and every schedule Lennox
#: ships has all seven days identical, so it was worked out by comparing the
#: startTime the LCC reports for the running zone period against the wall
#: clock. ``lennoxs30ctl schedule whatday`` re-runs that check on any system.
WEEKDAYS: tuple[str, ...] = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

PERIODS_PER_DAY = 4
SECONDS_PER_DAY = 24 * 60 * 60

#: Slots 5 - 15 arrive with no schedule object until one is created on the
#: panel, and we do not know the message that creates one.
EMPTY_SLOTS = range(5, 16)

#: From 16 up are the thermostat's own manual, away, hold and event schedules.
FIRST_INTERNAL_SLOT = 16


class ScheduleError(Exception):
    """Raised when a schedule or period cannot be addressed."""


def period_id(day_index: int, period: int) -> int:
    """Convert a day of the week and a period into a period id."""
    return day_index * PERIODS_PER_DAY + period


def start_time(day_index: int, when: time) -> int:
    """Convert a day and a time of day into seconds since the start of the week."""
    return day_index * SECONDS_PER_DAY + when.hour * 3600 + when.minute * 60


def day_of(week_seconds: int) -> int:
    """Return the day index a week-seconds value falls in."""
    return week_seconds // SECONDS_PER_DAY


def time_of(week_seconds: int) -> time:
    """Return the time of day a week-seconds value falls at."""
    within_day = week_seconds % SECONDS_PER_DAY
    return time(within_day // 3600, within_day % 3600 // 60)


def weekday_index(name: str) -> int:
    """Look up a weekday by name."""
    try:
        return WEEKDAYS.index(name.casefold())
    except ValueError:
        msg = f"Unknown day [{name}]. Use one of: {', '.join(WEEKDAYS)}"
        raise ScheduleError(msg) from None


class PeriodView(NamedTuple):
    """One period of a schedule."""

    id: int
    day: int
    slot: int
    enabled: bool
    starts: time | None
    heat: float | None
    cool: float | None
    single: float | None
    fan_mode: str | None
    system_mode: str | None
    humidity_mode: str | None
    unit: TempUnit


class ScheduleView(NamedTuple):
    """One schedule slot."""

    id: int
    name: str
    period_count: int
    editable: bool
    periods: tuple[PeriodView, ...]


def _period_view(period: Any, unit: TempUnit, single_mode: bool) -> PeriodView:
    """Build a typed snapshot of one period."""
    celsius = unit == "C"
    starts = None if period.startTime is None else time_of(int(period.startTime))
    return PeriodView(
        id=int(period.id),
        day=int(period.id) // PERIODS_PER_DAY,
        slot=int(period.id) % PERIODS_PER_DAY,
        enabled=bool(period.enabled),
        starts=starts,
        heat=None if single_mode else _num(period.hspC if celsius else period.hsp),
        cool=None if single_mode else _num(period.cspC if celsius else period.csp),
        single=_num(period.spC if celsius else period.sp) if single_mode else None,
        fan_mode=_text(period.fanMode),
        system_mode=_text(period.systemMode),
        humidity_mode=_text(period.humidityMode),
        unit=unit,
    )


def _num(value: Any) -> float | None:
    """Coerce a setpoint to a float."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str | None:
    """Coerce a mode to a string."""
    return None if value is None else str(value) or None


def schedule_view(system: Any, schedule: Any) -> ScheduleView:
    """Build a typed snapshot of a schedule."""
    unit = system_unit(system)
    single_mode = bool(system.single_setpoint_mode)
    periods = tuple(
        _period_view(period, unit, single_mode) for period in schedule.periods
    )
    return ScheduleView(
        id=int(schedule.id),
        name=str(schedule.name),
        period_count=int(schedule.periodCount),
        editable=int(schedule.id) < FIRST_INTERNAL_SLOT,
        periods=periods,
    )


def all_schedules(system: Any) -> list[ScheduleView]:
    """Every schedule slot the thermostat has reported."""
    return [schedule_view(system, s) for s in system.getSchedules()]


def find_schedule(system: Any, key: str | int) -> Any:
    """Look a schedule up by slot number or by name.

    Raises:
        ScheduleError: If it does not exist, naming the empty slots explicitly
            since those are the ones people reach for.
    """
    if isinstance(key, int) or (isinstance(key, str) and key.isdigit()):
        slot = int(key)
        schedule = system.getSchedule(slot)
        if schedule is not None:
            return schedule
        if slot in EMPTY_SLOTS:
            msg = (
                f"Schedule slot {slot} is empty. Slots "
                f"{EMPTY_SLOTS.start} - {EMPTY_SLOTS.stop - 1} stay empty until a "
                "schedule is created on the thermostat itself, and creating one "
                "over the API is not supported."
            )
            raise ScheduleError(msg)
        msg = f"No schedule in slot {slot}"
        raise ScheduleError(msg)

    lowered = str(key).casefold()
    for schedule in system.getSchedules():
        if str(schedule.name).casefold() == lowered:
            return schedule
    msg = f"No schedule named [{key}]. Run 'lennoxs30ctl schedule list' to see them."
    raise ScheduleError(msg)


def running_day(system: Any) -> tuple[int, str] | None:
    """Return the day index the thermostat currently thinks it is.

    Reads the ``startTime`` of the period a zone is actually running, which is
    the only place the LCC exposes its own idea of the weekday. Returns None
    when no zone is running a schedule.
    """
    for zone in system.zone_list:
        if not zone.is_zone_active() or zone.startTime is None:
            continue
        schedule_id = zone.scheduleId
        if schedule_id is not None and int(schedule_id) >= FIRST_INTERNAL_SLOT:
            # Manual mode, away or a hold - the startTime is not a weekday.
            continue
        index = day_of(int(zone.startTime))
        if 0 <= index < len(WEEKDAYS):
            return index, str(zone.name)
    return None


def format_period(period: PeriodView) -> str:
    """Format one period as a single line."""
    starts = "--:--" if period.starts is None else period.starts.strftime("%H:%M")
    if period.single is not None:
        setpoints = format_temp(period.single, period.unit)
    else:
        heat = format_temp(period.heat, period.unit)
        cool = format_temp(period.cool, period.unit)
        setpoints = f"{heat} / {cool}"
    state = "   " if period.enabled else "off"
    return (
        f"    {period.slot}  {starts}  {state}  {setpoints:<16} "
        f"{format_text(period.fan_mode):<10} {format_text(period.system_mode)}"
    )
