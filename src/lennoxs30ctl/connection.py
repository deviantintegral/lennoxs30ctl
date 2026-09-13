"""Connection lifecycle for a local Lennox S30.

The library is not request/response. Connecting means ``serverConnect()``, then
``subscribe()`` per system, then looping on ``messagePump()``; state arrives
asynchronously and is written onto the ``lennox_system`` and ``lennox_zone``
objects. On a LAN connection ``messagePump()`` long-polls for 15 seconds and
returns ``False`` when nothing arrived, so the loop needs no sleep of its own.

This module and :mod:`lennoxs30ctl.format` are the only ones that import
lennoxs30api.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from enum import Enum
from typing import TYPE_CHECKING, Any

from lennoxs30api.s30api_async import s30api_async
from lennoxs30api.s30exception import S30Exception

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from types import TracebackType

# Re-exported so the rest of the package can catch library errors without
# importing lennoxs30api itself.
__all__ = [
    "CONFIG_TIMEOUT",
    "ConnectionError_",
    "ConnectionState",
    "S30Connection",
    "S30Exception",
    "library_logging",
]

_LOGGER = logging.getLogger(__name__)

#: How long to wait for the thermostat to send its configuration after
#: subscribing. The panel usually answers in well under five seconds.
CONFIG_TIMEOUT = 30.0

#: Seconds between reconnect attempts after the pump drops.
RETRY_DELAY = 10.0

#: Pause after a read that returned nothing, so the loop can never busy-spin.
IDLE_DELAY = 0.5

#: Cap on the logout that runs at shutdown. Disconnecting has been known to
#: wedge the panel, so never block forever on it.
SHUTDOWN_TIMEOUT = 10.0


class ConnectionState(Enum):
    """Where the connection currently is."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RETRYING = "retrying"


class ConnectionError_(Exception):
    """Raised when the thermostat cannot be reached or does not answer in time."""


class S30Connection:
    """Owns the api object, the subscription, and the message pump task."""

    def __init__(
        self,
        host: str,
        app_id: str,
        *,
        message_logging: bool = False,
        api: Any | None = None,
    ) -> None:
        """Create a connection.

        Args:
            host: Hostname or IP of the thermostat.
            app_id: Subscription id; must not collide with another client.
            message_logging: Log the raw messages exchanged with the panel.
            api: An existing api object to drive, used by the tests.
        """
        self.host = host
        self.app_id = app_id
        self._api: Any = api or s30api_async(
            "",
            "",
            app_id,
            ip_address=host,
            message_debug_logging=message_logging,
            pii_message_logs=False,
        )
        self._pump_task: asyncio.Task[None] | None = None
        self._stopping = False
        self.state = ConnectionState.DISCONNECTED
        self._state_listeners: list[Callable[[ConnectionState], None]] = []

    # -- state ------------------------------------------------------------

    def add_state_listener(self, listener: Callable[[ConnectionState], None]) -> None:
        """Register a callback fired whenever the connection state changes."""
        self._state_listeners.append(listener)

    def _set_state(self, state: ConnectionState) -> None:
        if state == self.state:
            return
        self.state = state
        for listener in self._state_listeners:
            try:
                listener(state)
            except Exception:
                _LOGGER.exception("connection state listener failed")

    # -- systems ----------------------------------------------------------

    @property
    def systems(self) -> list[Any]:
        """The systems the thermostat has reported."""
        systems: list[Any] = list(self._api.system_list)
        return systems

    def system(self, sys_id: str | None = None) -> Any:
        """Return a system by id, or the only one when no id is given."""
        systems = self.systems
        if sys_id is None:
            if not systems:
                msg = "No systems reported by the thermostat"
                raise ConnectionError_(msg)
            return systems[0]
        for candidate in systems:
            if str(candidate.sysId) == sys_id:
                return candidate
        msg = f"Unknown system [{sys_id}]"
        raise ConnectionError_(msg)

    def zone(self, system: Any, key: str) -> Any:
        """Look a zone up by index or by name."""
        if key.isdigit():
            zone = system.getZone(int(key))
            if zone is not None:
                return zone
        lowered = key.casefold()
        for candidate in system.zone_list:
            name = candidate.name
            if name is not None and str(name).casefold() == lowered:
                return candidate
        msg = f"Unknown zone [{key}]. Run 'lennoxs30ctl list' to see the zones."
        raise ConnectionError_(msg)

    # -- lifecycle --------------------------------------------------------

    async def connect(self, *, config_timeout: float = CONFIG_TIMEOUT) -> None:
        """Connect, subscribe, and pump until the configuration has arrived."""
        self._set_state(ConnectionState.CONNECTING)
        try:
            await self._api.serverConnect()
            for system in self._api.system_list:
                await self._api.subscribe(system)
        except S30Exception as exc:
            self._set_state(ConnectionState.DISCONNECTED)
            msg = f"Could not connect to {self.host}: {exc.as_string()}"
            raise ConnectionError_(msg) from exc

        await self._wait_for_config(config_timeout)
        self._set_state(ConnectionState.CONNECTED)

    async def _wait_for_config(self, timeout: float) -> None:
        """Pump messages until every system has reported at least one zone."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if self._configured():
                return
            try:
                await self._api.messagePump()
            except S30Exception as exc:
                msg = f"Error while reading configuration: {exc.as_string()}"
                raise ConnectionError_(msg) from exc
        if not self._configured():
            self._set_state(ConnectionState.DISCONNECTED)
            msg = (
                f"Timed out after {timeout:.0f}s waiting for configuration from "
                f"{self.host}. If Home Assistant is connected to this thermostat, "
                "stop it first - the panel only handles one client at a time."
            )
            raise ConnectionError_(msg)

    def _configured(self) -> bool:
        """True once at least one system has named, active zones."""
        systems = self.systems
        if not systems:
            return False
        return all(
            any(zone.is_zone_active() for zone in system.zone_list)
            for system in systems
        )

    async def drain(self, seconds: float) -> None:
        """Keep reading messages for a while, so state catches up after a write.

        Used by the CLI to report the thermostat's own state back rather than
        whatever we just asked for.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + seconds
        while loop.time() < deadline:
            try:
                received = await self._api.messagePump()
            except S30Exception as exc:
                _LOGGER.warning("error while draining messages: %s", exc.as_string())
                return
            if not received:
                return
            # Yield, so a thermostat with a lot to say cannot starve the loop.
            await asyncio.sleep(0)

    def start_pump(self) -> None:
        """Start the background task that keeps state up to date."""
        if self._pump_task is None:
            self._pump_task = asyncio.create_task(self._pump_forever())

    async def _pump_forever(self) -> None:
        """Read messages until stopped, reconnecting when the connection drops."""
        while not self._stopping:
            try:
                received = await self._api.messagePump()
                self._set_state(ConnectionState.CONNECTED)
                if not received:
                    # A LAN connection long-polls, so this sleep almost never
                    # costs anything. Without it a pump that returns straight
                    # away - a cloud connection, or an error path - would spin
                    # and starve the event loop.
                    await asyncio.sleep(IDLE_DELAY)
            except asyncio.CancelledError:
                raise
            except S30Exception as exc:
                if self._stopping:
                    return
                _LOGGER.error("message pump failed: %s", exc.as_string())
                self._set_state(ConnectionState.RETRYING)
                await self._reconnect()
            except Exception:
                if self._stopping:
                    return
                _LOGGER.exception("message pump failed unexpectedly")
                self._set_state(ConnectionState.RETRYING)
                await self._reconnect()

    async def _reconnect(self) -> None:
        """Re-establish the subscription, waiting between attempts."""
        while not self._stopping:
            await asyncio.sleep(RETRY_DELAY)
            if self._stopping:
                return
            try:
                await self._api.serverConnect()
                for system in self._api.system_list:
                    await self._api.subscribe(system)
            except S30Exception as exc:
                _LOGGER.error("reconnect failed: %s", exc.as_string())
                continue
            self._set_state(ConnectionState.CONNECTED)
            return

    async def close(self) -> None:
        """Stop pumping and drop the subscription."""
        self._stopping = True
        if self._pump_task is not None:
            self._pump_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._pump_task
            self._pump_task = None
        with contextlib.suppress(Exception, asyncio.TimeoutError):
            async with asyncio.timeout(SHUTDOWN_TIMEOUT):
                await self._api.shutdown()
        self._set_state(ConnectionState.DISCONNECTED)

    async def __aenter__(self) -> S30Connection:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()


@contextlib.contextmanager
def library_logging(level: int) -> Iterator[None]:
    """Raise the lennoxs30api log level for the duration of the block."""
    logger = logging.getLogger("lennoxs30api")
    previous = logger.level
    logger.setLevel(level)
    try:
        yield
    finally:
        logger.setLevel(previous)
