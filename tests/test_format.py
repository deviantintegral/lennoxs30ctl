"""Tests for the typed views over the library objects."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from lennoxs30ctl.format import (
    DEFAULT_UNIT,
    _warn_unknown_unit,
    active_zone_views,
    available_humidity_modes,
    available_hvac_modes,
    format_bool,
    format_percent,
    format_setpoints,
    format_temp,
    format_text,
    preset_names,
    system_unit,
    system_view,
    zone_view,
)


class TestZoneView:
    """Zone snapshots read from the real captured state."""

    def test_identity(self, system: Any, zone: Any) -> None:
        view = zone_view(system, zone)
        assert view.zone_id == 0
        assert view.name == "Main Floor"
        assert view.active is True

    def test_uses_the_thermostats_own_unit(self, system: Any, zone: Any) -> None:
        """The capture is a Celsius system, so Celsius values are surfaced."""
        assert system.temperatureUnit == "C"
        view = zone_view(system, zone)
        assert view.unit == "C"
        assert view.temperature == zone.temperatureC
        assert view.setpoints.heat == zone.hspC
        assert view.setpoints.cool == zone.cspC

    def test_fahrenheit_system(self, system: Any, zone: Any) -> None:
        system.temperatureUnit = "F"
        view = zone_view(system, zone)
        assert view.unit == "F"
        assert view.temperature == zone.temperature
        assert view.setpoints.heat == zone.hsp

    def test_single_setpoint_mode(self, system: Any, zone: Any) -> None:
        system.single_setpoint_mode = True
        view = zone_view(system, zone)
        assert view.single_setpoint_mode is True
        assert view.setpoints.single == zone.spC
        assert view.setpoints.heat is None
        assert view.setpoints.cool is None

    def test_modes_and_operation(self, system: Any, zone: Any) -> None:
        view = zone_view(system, zone)
        assert view.hvac_mode == "heat and cool"
        assert view.fan_mode == zone.fanMode
        assert view.humidity_mode == zone.humidityMode

    def test_missing_name_falls_back(self, system: Any, zone: Any) -> None:
        zone.name = None
        assert zone_view(system, zone).name == "Zone 1"

    def test_preset_is_none_without_schedules(self, system: Any, zone: Any) -> None:
        """This capture has no schedules block, so there is no preset to show."""
        assert zone_view(system, zone).preset is None

    def test_preset_resolves_when_schedules_exist(
        self, api_with_schedules: Any
    ) -> None:
        system = api_with_schedules.system_list[0]
        zone = system.zone_list[0]
        zone.scheduleId = 1
        assert zone_view(system, zone).preset == "summer"

    def test_unknown_schedule_id(self, api_with_schedules: Any) -> None:
        system = api_with_schedules.system_list[0]
        zone = system.zone_list[0]
        zone.scheduleId = 999
        assert zone_view(system, zone).preset is None


class TestActiveZones:
    """Only zones the thermostat reports as active are shown."""

    def test_inactive_zone_excluded(self, system: Any) -> None:
        views = active_zone_views(system)
        assert [v.name for v in views] == ["Main Floor", "Upper Floor", "Basement"]
        assert all(v.active for v in views)


class TestSystemView:
    """System snapshots."""

    def test_fields(self, system: Any) -> None:
        view = system_view(system)
        assert view.sys_id == "LCC"
        assert view.name == "System"
        assert view.unit == "C"
        assert view.outdoor_temperature == system.outdoorTemperatureC
        assert view.software_version == "3.81.213"
        assert view.away_mode is False

    def test_fahrenheit_outdoor(self, system: Any) -> None:
        system.temperatureUnit = "F"
        assert system_view(system).outdoor_temperature == system.outdoorTemperature

    def test_name_falls_back_to_sys_id(self, system: Any) -> None:
        system.name = None
        assert system_view(system).name == "LCC"

    def test_schedule_names(self, api_with_schedules: Any) -> None:
        view = system_view(api_with_schedules.system_list[0])
        assert "summer" in view.schedule_names


class TestUnitDetection:
    """The unit comes from the thermostat's own configuration."""

    def test_celsius(self, system: Any) -> None:
        system.temperatureUnit = "C"
        assert system_unit(system) == "C"

    def test_fahrenheit(self, system: Any) -> None:
        system.temperatureUnit = "F"
        assert system_unit(system) == "F"

    def test_unknown_falls_back(self, system: Any) -> None:
        system.temperatureUnit = "kelvin"
        assert system_unit(system) == DEFAULT_UNIT

    def test_missing_falls_back(self, system: Any) -> None:
        system.temperatureUnit = None
        assert system_unit(system) == DEFAULT_UNIT


class TestUnknownUnitIsAnnounced:
    """Guessing the unit silently produced plausible but mislabelled output."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self) -> None:
        _warn_unknown_unit.cache_clear()

    def test_missing_unit_warns(
        self, system: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        system.temperatureUnit = None
        with caplog.at_level(logging.WARNING):
            assert system_unit(system) == DEFAULT_UNIT
        assert "temperature unit" in caplog.text
        assert DEFAULT_UNIT in caplog.text

    def test_unrecognised_unit_warns(
        self, system: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        system.temperatureUnit = "kelvin"
        with caplog.at_level(logging.WARNING):
            system_unit(system)
        assert "kelvin" in caplog.text

    def test_warns_once_not_per_value(
        self, system: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        """system_unit runs for every rendered value; do not bury the output."""
        system.temperatureUnit = None
        with caplog.at_level(logging.WARNING):
            for _ in range(20):
                system_unit(system)
        assert caplog.text.count("temperature unit") == 1

    def test_a_known_unit_is_quiet(
        self, system: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        system.temperatureUnit = "C"
        with caplog.at_level(logging.WARNING):
            assert system_unit(system) == "C"
        assert caplog.text == ""


class TestAvailableModes:
    """Mode lists follow the zone's own equipment options."""

    def test_hvac_modes_for_heat_and_cool(self, zone: Any) -> None:
        assert available_hvac_modes(zone) == ("off", "cool", "heat", "heat and cool")

    def test_hvac_modes_cooling_only(self, zone: Any) -> None:
        zone.heatingOption = False
        assert available_hvac_modes(zone) == ("off", "cool")

    def test_hvac_modes_with_emergency_heat(self, zone: Any) -> None:
        zone.emergencyHeatingOption = True
        assert "emergency heat" in available_hvac_modes(zone)

    def test_humidity_modes(self, zone: Any) -> None:
        zone.humidificationOption = True
        zone.dehumidificationOption = True
        assert available_humidity_modes(zone) == (
            "off",
            "dehumidify",
            "humidify",
            "both",
        )

    def test_humidity_modes_none_available(self, zone: Any) -> None:
        zone.humidificationOption = False
        zone.dehumidificationOption = False
        assert available_humidity_modes(zone) == ("off",)


class TestPresetNames:
    """Internal schedule slots are not offered as presets."""

    def test_excludes_internal_slots(self, api_with_schedules: Any) -> None:
        names = preset_names(api_with_schedules.system_list[0])
        assert "summer" in names
        assert not any(n.startswith("manual zone") for n in names)
        assert "activeEvent" not in names


class TestFormatting:
    """Display helpers."""

    def test_temp(self) -> None:
        assert format_temp(20.0, "C") == "20°C"
        assert format_temp(20.5, "C") == "20.5°C"
        assert format_temp(None, "F") == "--"

    def test_percent(self) -> None:
        assert format_percent(40) == "40%"
        assert format_percent(None) == "--"

    def test_bool(self) -> None:
        assert format_bool(True) == "on"
        assert format_bool(False) == "off"
        assert format_bool(None) == "--"

    def test_text(self) -> None:
        assert format_text("heat") == "heat"
        assert format_text(None) == "--"
        assert format_text("") == "--"

    def test_setpoints_split(self, system: Any, zone: Any) -> None:
        view = zone_view(system, zone)
        assert format_setpoints(view) == "18°C / 22°C"

    def test_setpoints_single(self, system: Any, zone: Any) -> None:
        system.single_setpoint_mode = True
        view = zone_view(system, zone)
        assert format_setpoints(view) == format_temp(zone.spC, "C")
