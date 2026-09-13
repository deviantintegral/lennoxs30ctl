"""Shared fixtures for lennoxs30ctl tests.

The fixtures under ``tests/fixtures`` are real captured messages from a four
zone LAN system, copied from the MIT licensed lennoxs30api test suite. Feeding
them through ``processMessage`` gives real library state with no network.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from lennoxs30api.s30api_async import s30api_async

from lennoxs30ctl.connection import S30Connection

FIXTURES_DIR = Path(__file__).parent / "fixtures"

_SYSTEM_04_MESSAGES = (
    "system_04_furn_ac_zoning_config.json",
    "system_04_furn_ac_zoning_equipment.json",
    "system_04_furn_ac_zoning_devices.json",
    "system_04_furn_ac_zoning_zones.json",
    "system_04_furn_ac_zoning_rgw.json",
    "system_04_furn_ac_zoning_alerts.json",
    "system_04_furn_ac_zoning_indoorAirQuality.json",
)


def loadfile(name: str, sys_id: str | None = None) -> Any:
    """Load one captured message."""
    data = json.loads((FIXTURES_DIR / name).read_text())
    if sys_id is not None:
        data["SenderID"] = sys_id
    return data


@pytest.fixture
def api() -> Any:
    """An api object loaded with the four zone LAN system."""
    api_ret = s30api_async("", "", "test", ip_address="10.0.0.1")
    api_ret.setup_local_homes()
    for name in _SYSTEM_04_MESSAGES:
        api_ret.processMessage(loadfile(name, "LCC"))
    return api_ret


@pytest.fixture
def api_with_schedules() -> Any:
    """An api object whose config includes the 48 schedule slots.

    The system_04 capture has no ``schedules`` block, so anything touching
    presets uses this one instead.
    """
    api_ret = s30api_async("", "", "test", None)
    api_ret.process_login_response(loadfile("login_response.json"))
    api_ret.processMessage(loadfile("config_response_system_01.json"))
    return api_ret


@pytest.fixture
def system(api: Any) -> Any:
    """The system from the loaded api object."""
    return api.system_list[0]


@pytest.fixture
def zone(system: Any) -> Any:
    """The first zone of the loaded system."""
    return system.zone_list[0]


@pytest.fixture
def connection(api: Any) -> S30Connection:
    """A connection driving the loaded api object, with the network stubbed out."""
    conn = S30Connection("thermostat.lan", "test", api=api)
    api.serverConnect = AsyncMock()
    api.subscribe = AsyncMock()
    api.messagePump = AsyncMock(return_value=False)
    api.shutdown = AsyncMock()
    return conn
