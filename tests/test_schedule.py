"""Tests for schedule reading and the schedule subcommands."""

from __future__ import annotations

from datetime import time
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from lennoxs30ctl.cli import (
    _parse_days,
    _parse_time,
    build_parser,
    cmd_schedule_list,
    cmd_schedule_rename,
    cmd_schedule_set,
    cmd_schedule_show,
    cmd_schedule_whatday,
)
from lennoxs30ctl.schedule import (
    WEEKDAYS,
    ScheduleError,
    all_schedules,
    day_of,
    find_schedule,
    period_id,
    running_day,
    schedule_view,
    start_time,
    time_of,
    weekday_index,
)

if TYPE_CHECKING:
    from lennoxs30ctl.connection import S30Connection


@pytest.fixture
def sched_conn(api_with_schedules: Any) -> Any:
    """A connection over the capture that actually carries schedules."""
    from lennoxs30ctl.connection import S30Connection

    conn = S30Connection("thermostat.lan", "test", api=api_with_schedules)
    api_with_schedules.messagePump = AsyncMock(return_value=False)
    return conn


class TestConversions:
    """Period ids and week seconds."""

    def test_period_id(self) -> None:
        assert period_id(0, 0) == 0
        assert period_id(1, 0) == 4
        assert period_id(6, 3) == 27

    def test_start_time(self) -> None:
        assert start_time(0, time(6, 0)) == 21600
        # the shipped summer schedule has day 1 period 0 at 06:00
        assert start_time(1, time(6, 0)) == 108000
        assert start_time(6, time(21, 0)) == 594000

    def test_round_trip(self) -> None:
        for day in range(7):
            for when in (time(0, 0), time(6, 30), time(23, 59)):
                seconds = start_time(day, when)
                assert day_of(seconds) == day
                assert time_of(seconds).hour == when.hour
                assert time_of(seconds).minute == when.minute

    def test_weekday_index(self) -> None:
        assert weekday_index("monday") == 0
        assert weekday_index("SUNDAY") == 6

    def test_unknown_weekday(self) -> None:
        with pytest.raises(ScheduleError, match="Unknown day"):
            weekday_index("funday")


class TestScheduleView:
    """Reading a schedule out of the captured config."""

    def test_summer_is_28_periods(self, api_with_schedules: Any) -> None:
        system = api_with_schedules.system_list[0]
        view = schedule_view(system, system.getSchedule(1))
        assert view.name == "summer"
        assert view.period_count == 28
        assert len(view.periods) == 28
        assert [p.id for p in view.periods] == list(range(28))

    def test_period_day_and_slot(self, api_with_schedules: Any) -> None:
        system = api_with_schedules.system_list[0]
        view = schedule_view(system, system.getSchedule(1))
        period = view.periods[4]
        assert (period.day, period.slot) == (1, 0)
        assert period.starts == time(6, 0)

    def test_editable_flag(self, api_with_schedules: Any) -> None:
        system = api_with_schedules.system_list[0]
        assert schedule_view(system, system.getSchedule(1)).editable is True
        assert schedule_view(system, system.getSchedule(16)).editable is False

    def test_disabled_periods_are_reported(self, api_with_schedules: Any) -> None:
        """schedule IQ ships with two of every day's periods turned off."""
        system = api_with_schedules.system_list[0]
        view = schedule_view(system, system.getSchedule(0))
        assert view.name == "schedule IQ"
        assert [p.enabled for p in view.periods[:4]] == [True, False, False, True]

    def test_all_schedules(self, api_with_schedules: Any) -> None:
        views = all_schedules(api_with_schedules.system_list[0])
        assert len(views) == 37  # the empty slots carry no schedule object


class TestFindSchedule:
    """Addressing a schedule."""

    def test_by_name(self, api_with_schedules: Any) -> None:
        system = api_with_schedules.system_list[0]
        assert find_schedule(system, "summer").id == 1

    def test_by_name_is_case_insensitive(self, api_with_schedules: Any) -> None:
        system = api_with_schedules.system_list[0]
        assert find_schedule(system, "SUMMER").id == 1

    def test_by_slot(self, api_with_schedules: Any) -> None:
        system = api_with_schedules.system_list[0]
        assert find_schedule(system, "1").name == "summer"
        assert find_schedule(system, 1).name == "summer"

    def test_empty_slot_says_so(self, api_with_schedules: Any) -> None:
        system = api_with_schedules.system_list[0]
        with pytest.raises(ScheduleError, match="stay empty until"):
            find_schedule(system, "7")

    def test_unknown_slot(self, api_with_schedules: Any) -> None:
        system = api_with_schedules.system_list[0]
        with pytest.raises(ScheduleError, match="No schedule in slot"):
            find_schedule(system, "99")

    def test_unknown_name(self, api_with_schedules: Any) -> None:
        system = api_with_schedules.system_list[0]
        with pytest.raises(ScheduleError, match="No schedule named"):
            find_schedule(system, "autumn")


class TestRunningDay:
    """The day 0 check reads the running zone's period."""

    def test_reports_the_day(self, api_with_schedules: Any) -> None:
        system = api_with_schedules.system_list[0]
        zone = system.zone_list[0]
        zone.scheduleId = 1
        zone.startTime = 5 * 86400 + 25200  # day 5, 07:00
        assert running_day(system) == (5, zone.name)

    def test_ignores_manual_mode(self, api_with_schedules: Any) -> None:
        """In manual mode the startTime is not a weekday."""
        system = api_with_schedules.system_list[0]
        for zone in system.zone_list:
            zone.scheduleId = zone.getManualModeScheduleId()
            zone.startTime = 0
        assert running_day(system) is None

    def test_none_when_no_start_time(self, api_with_schedules: Any) -> None:
        system = api_with_schedules.system_list[0]
        for zone in system.zone_list:
            zone.scheduleId = 1
            zone.startTime = None
        assert running_day(system) is None


class TestDayArguments:
    """The --days argument."""

    def test_all(self) -> None:
        assert _parse_days("all") == list(WEEKDAYS)

    def test_comma_separated(self) -> None:
        assert _parse_days("monday,friday") == ["monday", "friday"]

    def test_whitespace_and_case(self) -> None:
        assert _parse_days(" Monday , FRIDAY ") == ["monday", "friday"]

    def test_empty(self) -> None:
        with pytest.raises(ValueError, match="No days given"):
            _parse_days(",")

    def test_invalid_day(self) -> None:
        with pytest.raises(ScheduleError, match="Unknown day"):
            _parse_days("monday,funday")

    def test_parse_time(self) -> None:
        assert _parse_time("06:30") == time(6, 30)

    def test_bad_time(self) -> None:
        with pytest.raises(ValueError, match="HH:MM"):
            _parse_time("half six")


class TestScheduleCommands:
    """The schedule subcommands."""

    async def test_list(
        self, sched_conn: S30Connection, capsys: pytest.CaptureFixture[str]
    ) -> None:
        await cmd_schedule_list(sched_conn)
        out = capsys.readouterr().out
        assert "summer" in out
        assert "manual zone 0" in out
        assert "5-15" in out

    async def test_show(
        self, sched_conn: S30Connection, capsys: pytest.CaptureFixture[str]
    ) -> None:
        await cmd_schedule_show(sched_conn, "summer")
        out = capsys.readouterr().out
        assert "summer" in out
        for day in WEEKDAYS:
            assert day in out
        assert "06:00" in out

    async def test_show_spells_out_both_states(
        self, sched_conn: S30Connection, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A blank cell under a state column reads as missing data."""
        await cmd_schedule_show(sched_conn, "schedule IQ")
        out = capsys.readouterr().out
        assert " on " in out
        assert " off " in out

    async def test_show_columns_line_up(
        self, sched_conn: S30Connection, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The header and the rows are built side by side; keep them aligned."""
        from lennoxs30ctl.schedule import PERIOD_HEADER

        await cmd_schedule_show(sched_conn, "summer")
        lines = capsys.readouterr().out.splitlines()
        header = next(line for line in lines if line == PERIOD_HEADER)
        row = lines[lines.index(header) + 1]
        for column in ("start", "state", "setpoints", "fan"):
            assert row[header.index(column)] != " ", f"{column} column is misaligned"

    async def test_set_writes_each_day(
        self, sched_conn: S30Connection, capsys: pytest.CaptureFixture[str]
    ) -> None:
        system = sched_conn.system()
        system.set_schedule_period = AsyncMock()
        await cmd_schedule_set(
            sched_conn,
            "summer",
            ["monday", "saturday"],
            0,
            {"start_time": time(6, 30), "heat": 19.0},
        )
        assert system.set_schedule_period.await_count == 2
        first = system.set_schedule_period.await_args_list[0]
        assert first[0] == (1, 0)
        assert first[1]["startTime"] == 23400
        # this capture is a fahrenheit system
        assert first[1]["hsp"] == 19.0
        second = system.set_schedule_period.await_args_list[1]
        assert second[0] == (1, 20)
        assert second[1]["startTime"] == 5 * 86400 + 23400

    async def test_set_follows_the_thermostats_unit(
        self, sched_conn: S30Connection
    ) -> None:
        """Setpoints go out in whichever unit the panel is configured for."""
        system = sched_conn.system()
        assert system.temperatureUnit == "F"
        system.set_schedule_period = AsyncMock()
        await cmd_schedule_set(
            sched_conn, "summer", ["monday"], 0, {"heat": 64.0, "cool": 76.0}
        )
        kwargs = system.set_schedule_period.await_args[1]
        assert kwargs["hsp"] == 64.0
        assert kwargs["csp"] == 76.0

        system.temperatureUnit = "C"
        system.set_schedule_period = AsyncMock()
        await cmd_schedule_set(
            sched_conn, "summer", ["monday"], 0, {"heat": 18.0, "cool": 24.0}
        )
        kwargs = system.set_schedule_period.await_args[1]
        assert kwargs["hspC"] == 18.0
        assert kwargs["cspC"] == 24.0

    async def test_set_disables_a_period(self, sched_conn: S30Connection) -> None:
        system = sched_conn.system()
        system.set_schedule_period = AsyncMock()
        await cmd_schedule_set(sched_conn, "summer", ["monday"], 1, {"enabled": False})
        assert system.set_schedule_period.await_args[1]["enabled"] is False

    async def test_set_passes_the_modes(self, sched_conn: S30Connection) -> None:
        system = sched_conn.system()
        system.set_schedule_period = AsyncMock()
        await cmd_schedule_set(
            sched_conn,
            "summer",
            ["monday"],
            0,
            {
                "fan_mode": "circulate",
                "system_mode": "heat",
                "humidity_mode": "off",
                "humidify": 40,
                "dehumidify": 55,
            },
        )
        kwargs = system.set_schedule_period.await_args[1]
        assert kwargs["fanMode"] == "circulate"
        assert kwargs["systemMode"] == "heat"
        assert kwargs["humidityMode"] == "off"
        assert kwargs["husp"] == 40
        assert kwargs["desp"] == 55

    async def test_rename(
        self, sched_conn: S30Connection, capsys: pytest.CaptureFixture[str]
    ) -> None:
        system = sched_conn.system()
        system.set_schedule_name = AsyncMock()
        await cmd_schedule_rename(sched_conn, "save energy", "shoulder season")
        system.set_schedule_name.assert_awaited_once_with(4, "shoulder season")
        assert "save energy" in capsys.readouterr().out

    async def test_whatday(
        self, sched_conn: S30Connection, capsys: pytest.CaptureFixture[str]
    ) -> None:
        system = sched_conn.system()
        zone = system.zone_list[0]
        zone.scheduleId = 1
        zone.startTime = 5 * 86400 + 25200
        await cmd_schedule_whatday(sched_conn)
        out = capsys.readouterr().out
        assert "day index 5" in out
        assert "saturday" in out

    async def test_whatday_without_a_schedule(
        self, sched_conn: S30Connection, capsys: pytest.CaptureFixture[str]
    ) -> None:
        system = sched_conn.system()
        for zone in system.zone_list:
            zone.startTime = None
        await cmd_schedule_whatday(sched_conn)
        assert "No zone is running a schedule" in capsys.readouterr().out


class TestScheduleParser:
    """The schedule subcommand arguments."""

    def test_show_requires_a_schedule(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["schedule", "show"])

    def test_set_requires_days_and_period(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["schedule", "set", "summer"])

    def test_set_rejects_a_fifth_period(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(
                ["schedule", "set", "summer", "--days", "all", "--period", "4"]
            )

    def test_set_accepts_the_full_form(self) -> None:
        args = build_parser().parse_args(
            [
                "schedule", "set", "summer",
                "--days", "monday,tuesday",
                "--period", "2",
                "--start", "17:30",
                "--heat", "19",
                "--fan-mode", "circulate",
            ]
        )  # fmt: skip
        assert args.schedule == "summer"
        assert args.days == "monday,tuesday"
        assert args.period == 2
        assert args.start == "17:30"
        assert args.heat == 19.0
        assert args.fan_mode == "circulate"

    def test_subcommand_is_required(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["schedule"])


class TestScheduleDispatch:
    """Routing from parsed arguments through to the library call."""

    async def _run(self, conn: Any, argv: list[str]) -> None:
        from lennoxs30ctl.cli import _dispatch_schedule

        await _dispatch_schedule(conn, build_parser().parse_args(argv))

    async def test_list(
        self, sched_conn: Any, capsys: pytest.CaptureFixture[str]
    ) -> None:
        await self._run(sched_conn, ["schedule", "list"])
        assert "summer" in capsys.readouterr().out

    async def test_show(
        self, sched_conn: Any, capsys: pytest.CaptureFixture[str]
    ) -> None:
        await self._run(sched_conn, ["schedule", "show", "summer"])
        assert "monday" in capsys.readouterr().out

    async def test_whatday(
        self, sched_conn: Any, capsys: pytest.CaptureFixture[str]
    ) -> None:
        await self._run(sched_conn, ["schedule", "whatday"])
        assert capsys.readouterr().out

    async def test_rename(self, sched_conn: Any) -> None:
        sched_conn.system().set_schedule_name = AsyncMock()
        await self._run(sched_conn, ["schedule", "rename", "4", "shoulder"])
        sched_conn.system().set_schedule_name.assert_awaited_once_with(4, "shoulder")

    async def test_set_maps_every_argument(self, sched_conn: Any) -> None:
        system = sched_conn.system()
        system.set_schedule_period = AsyncMock()
        await self._run(
            sched_conn,
            [
                "schedule", "set", "summer",
                "--days", "tuesday",
                "--period", "2",
                "--start", "17:30",
                "--enabled", "on",
                "--heat", "64",
                "--cool", "76",
                "--humidify", "40",
                "--dehumidify", "55",
                "--fan-mode", "circulate",
                "--system-mode", "heat and cool",
                "--humidity-mode", "off",
            ],
        )  # fmt: skip
        args = system.set_schedule_period.await_args[0]
        kwargs = system.set_schedule_period.await_args[1]
        # tuesday is day 1, period 2, so the period id is 6
        assert args == (1, 6)
        assert kwargs["startTime"] == 86400 + 17 * 3600 + 30 * 60
        assert kwargs["enabled"] is True
        assert kwargs["hsp"] == 64.0
        assert kwargs["csp"] == 76.0
        assert kwargs["husp"] == 40
        assert kwargs["desp"] == 55
        assert kwargs["fanMode"] == "circulate"
        assert kwargs["systemMode"] == "heat and cool"
        assert kwargs["humidityMode"] == "off"

    async def test_set_off_disables(self, sched_conn: Any) -> None:
        system = sched_conn.system()
        system.set_schedule_period = AsyncMock()
        await self._run(
            sched_conn,
            ["schedule", "set", "summer", "--days", "all", "--period", "1",
             "--enabled", "off"],
        )  # fmt: skip
        assert system.set_schedule_period.await_count == 7
        assert system.set_schedule_period.await_args[1]["enabled"] is False


class TestFormatEdges:
    """Formatting periods that are missing values."""

    def test_single_setpoint_period(self, api_with_schedules: Any) -> None:
        from lennoxs30ctl.schedule import format_period

        system = api_with_schedules.system_list[0]
        system.single_setpoint_mode = True
        view = schedule_view(system, system.getSchedule(1))
        assert "/" not in format_period(view.periods[0])

    def test_unparseable_setpoint(self) -> None:
        from lennoxs30ctl.schedule import _num, _text

        assert _num("warm") is None
        assert _num(None) is None
        assert _text(None) is None
