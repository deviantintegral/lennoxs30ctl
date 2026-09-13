"""Tests for the connection lifecycle."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock

import pytest
from lennoxs30api.s30exception import EC_COMMS_ERROR, S30Exception

from lennoxs30ctl.connection import (
    ConnectionError_,
    ConnectionState,
    S30Connection,
    library_logging,
)


class TestLookup:
    """Finding systems and zones."""

    def test_systems(self, connection: S30Connection) -> None:
        assert [s.sysId for s in connection.systems] == ["LCC"]

    def test_default_system(self, connection: S30Connection) -> None:
        assert connection.system().sysId == "LCC"

    def test_system_by_id(self, connection: S30Connection) -> None:
        assert connection.system("LCC").sysId == "LCC"

    def test_unknown_system(self, connection: S30Connection) -> None:
        with pytest.raises(ConnectionError_, match="Unknown system"):
            connection.system("nope")

    def test_no_systems(self) -> None:
        api = AsyncMock()
        api.system_list = []
        conn = S30Connection("host", "id", api=api)
        with pytest.raises(ConnectionError_, match="No systems"):
            conn.system()

    def test_zone_by_index(self, connection: S30Connection) -> None:
        system = connection.system()
        assert connection.zone(system, "0").name == "Main Floor"

    def test_zone_by_name(self, connection: S30Connection) -> None:
        system = connection.system()
        assert connection.zone(system, "Upper Floor").id == 1

    def test_zone_name_is_case_insensitive(self, connection: S30Connection) -> None:
        system = connection.system()
        assert connection.zone(system, "upper floor").id == 1

    def test_unknown_zone(self, connection: S30Connection) -> None:
        system = connection.system()
        with pytest.raises(ConnectionError_, match="Unknown zone"):
            connection.zone(system, "Attic")

    def test_out_of_range_index_falls_through_to_name(
        self, connection: S30Connection
    ) -> None:
        system = connection.system()
        with pytest.raises(ConnectionError_, match="Unknown zone"):
            connection.zone(system, "99")


class TestConnect:
    """Connecting, subscribing and waiting for the configuration."""

    async def test_connect_subscribes(self, connection: S30Connection) -> None:
        await connection.connect()
        assert connection.state == ConnectionState.CONNECTED
        connection._api.serverConnect.assert_awaited_once()
        connection._api.subscribe.assert_awaited_once()

    async def test_connect_failure(self, connection: S30Connection) -> None:
        connection._api.serverConnect.side_effect = S30Exception(
            "boom", EC_COMMS_ERROR, 1
        )
        with pytest.raises(ConnectionError_, match="Could not connect"):
            await connection.connect()
        assert connection.state == ConnectionState.DISCONNECTED

    async def test_config_timeout_mentions_home_assistant(self, api: Any) -> None:
        """The usual cause of a timeout is another client holding the panel."""
        for zone in api.system_list[0].zone_list:
            zone.temperature = None
        conn = S30Connection("host", "id", api=api)
        api.serverConnect = AsyncMock()
        api.subscribe = AsyncMock()
        api.messagePump = AsyncMock(return_value=False)
        with pytest.raises(ConnectionError_, match="Home Assistant"):
            await conn.connect(config_timeout=0.05)

    async def test_pump_error_while_waiting(self, api: Any) -> None:
        for zone in api.system_list[0].zone_list:
            zone.temperature = None
        conn = S30Connection("host", "id", api=api)
        api.serverConnect = AsyncMock()
        api.subscribe = AsyncMock()
        api.messagePump = AsyncMock(side_effect=S30Exception("x", EC_COMMS_ERROR, 1))
        with pytest.raises(ConnectionError_, match="reading configuration"):
            await conn.connect(config_timeout=1.0)


class TestStateListeners:
    """State changes are published to listeners."""

    async def test_listener_called(self, connection: S30Connection) -> None:
        seen: list[ConnectionState] = []
        connection.add_state_listener(seen.append)
        await connection.connect()
        assert ConnectionState.CONNECTED in seen

    async def test_listener_failure_is_contained(
        self, connection: S30Connection, caplog: pytest.LogCaptureFixture
    ) -> None:
        def boom(state: ConnectionState) -> None:
            raise RuntimeError("listener exploded")

        connection.add_state_listener(boom)
        with caplog.at_level(logging.ERROR):
            await connection.connect()
        assert "listener failed" in caplog.text

    async def test_no_event_when_unchanged(self, connection: S30Connection) -> None:
        await connection.connect()
        seen: list[ConnectionState] = []
        connection.add_state_listener(seen.append)
        connection._set_state(ConnectionState.CONNECTED)
        assert seen == []


class TestDrain:
    """Draining reads until the thermostat goes quiet."""

    async def test_stops_when_nothing_received(self, connection: S30Connection) -> None:
        connection._api.messagePump = AsyncMock(return_value=False)
        await connection.drain(5.0)
        assert connection._api.messagePump.await_count == 1

    async def test_reads_while_messages_arrive(self, connection: S30Connection) -> None:
        connection._api.messagePump = AsyncMock(side_effect=[True, True, False])
        await connection.drain(5.0)
        assert connection._api.messagePump.await_count == 3

    async def test_errors_stop_the_drain(
        self, connection: S30Connection, caplog: pytest.LogCaptureFixture
    ) -> None:
        connection._api.messagePump = AsyncMock(
            side_effect=S30Exception("x", EC_COMMS_ERROR, 1)
        )
        with caplog.at_level(logging.WARNING):
            await connection.drain(5.0)
        assert "draining" in caplog.text

    async def test_zero_budget_reads_nothing(self, connection: S30Connection) -> None:
        await connection.drain(0)
        assert connection._api.messagePump.await_count == 0


class TestPump:
    """The background pump task."""

    async def test_pump_runs_and_stops(
        self, connection: S30Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("lennoxs30ctl.connection.IDLE_DELAY", 0.01)
        await connection.connect()
        connection.start_pump()
        connection.start_pump()  # idempotent
        await asyncio.sleep(0)
        assert connection._pump_task is not None
        await connection.close()
        assert connection._pump_task is None

    async def test_pump_reconnects_after_failure(
        self, connection: S30Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("lennoxs30ctl.connection.RETRY_DELAY", 0)
        monkeypatch.setattr("lennoxs30ctl.connection.IDLE_DELAY", 0.01)
        calls = {"n": 0}

        async def pump() -> bool:
            calls["n"] += 1
            if calls["n"] == 1:
                raise S30Exception("dropped", EC_COMMS_ERROR, 1)
            await asyncio.sleep(0.01)
            return False

        connection._api.messagePump = pump
        await connection.connect()
        connection.start_pump()
        await asyncio.sleep(0.05)
        await connection.close()
        assert connection._api.serverConnect.await_count >= 2

    async def test_pump_handles_unexpected_errors(
        self, connection: S30Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("lennoxs30ctl.connection.RETRY_DELAY", 0)
        monkeypatch.setattr("lennoxs30ctl.connection.IDLE_DELAY", 0.01)
        calls = {"n": 0}

        async def pump() -> bool:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("something odd")
            await asyncio.sleep(0.01)
            return False

        connection._api.messagePump = pump
        await connection.connect()
        connection.start_pump()
        await asyncio.sleep(0.05)
        await connection.close()
        assert calls["n"] >= 2

    async def test_reconnect_retries_until_it_works(
        self, connection: S30Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("lennoxs30ctl.connection.RETRY_DELAY", 0)
        monkeypatch.setattr("lennoxs30ctl.connection.IDLE_DELAY", 0.01)
        connection._api.serverConnect = AsyncMock(
            side_effect=[S30Exception("no", EC_COMMS_ERROR, 1), None]
        )
        await connection._reconnect()
        assert connection.state == ConnectionState.CONNECTED


class TestClose:
    """Shutdown drops the subscription."""

    async def test_close_calls_shutdown(self, connection: S30Connection) -> None:
        await connection.connect()
        await connection.close()
        connection._api.shutdown.assert_awaited_once()
        assert connection.state == ConnectionState.DISCONNECTED

    async def test_close_survives_a_wedged_panel(
        self, connection: S30Connection
    ) -> None:
        """Disconnecting has been known to hang; it must not hang us."""
        connection._api.shutdown = AsyncMock(side_effect=RuntimeError("wedged"))
        await connection.close()
        assert connection.state == ConnectionState.DISCONNECTED

    async def test_context_manager(self, connection: S30Connection) -> None:
        async with connection as conn:
            assert conn.state == ConnectionState.CONNECTED
        assert connection.state == ConnectionState.DISCONNECTED


class TestLibraryLogging:
    """The library log level is restored afterwards."""

    def test_restores_level(self) -> None:
        logger = logging.getLogger("lennoxs30api")
        logger.setLevel(logging.WARNING)
        with library_logging(logging.DEBUG):
            assert logger.level == logging.DEBUG
        assert logger.level == logging.WARNING


class TestIdleDelay:
    """A pump that returns instantly must not starve the event loop."""

    async def test_idle_pump_yields(
        self, connection: S30Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("lennoxs30ctl.connection.IDLE_DELAY", 0.01)
        connection._api.messagePump = AsyncMock(return_value=False)
        await connection.connect()
        connection.start_pump()

        # If the loop spun, this sleep would never get a turn to run.
        ticks = 0
        for _ in range(5):
            await asyncio.sleep(0.01)
            ticks += 1
        await connection.close()
        assert ticks == 5

    async def test_drain_yields_between_messages(
        self, connection: S30Connection
    ) -> None:
        connection._api.messagePump = AsyncMock(side_effect=[True] * 3 + [False])
        await connection.drain(5.0)
        assert connection._api.messagePump.await_count == 4
