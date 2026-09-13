"""Connection settings, resolved from flags, the environment, or a config file."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import socket
import tomllib
from pathlib import Path
from typing import NamedTuple

ENV_HOST = "LENNOXS30_HOST"
ENV_APP_ID = "LENNOXS30_APP_ID"

#: The app_id is the endpoint identity on the thermostat - it is the path
#: segment in /Endpoints/{app_id}/Connect and /Messages/{app_id}/Retrieve - so
#: every client needs its own, and the Home Assistant integration already uses
#: "homeassistant". Keeping a readable prefix means a message log or a panel
#: that will not connect still says which client it belongs to.
APP_ID_PREFIX = "lennoxs30ctl"

#: Lennox's own ids are around 28 characters, so there is plenty of room.
_APP_ID_ENTROPY_BYTES = 4

#: It ends up in a URL path, so keep it to characters that need no escaping.
_APP_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class ConfigError(Exception):
    """Raised when the settings are incomplete or the config file is unreadable."""


class Settings(NamedTuple):
    """Everything needed to open a connection."""

    host: str
    app_id: str


def config_path() -> Path:
    """Return the path of the config file, honouring XDG_CONFIG_HOME."""
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "lennoxs30ctl" / "config.toml"


def _load_file(path: Path) -> dict[str, object]:
    """Read the config file, returning an empty mapping when it is absent."""
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        msg = f"Could not read {path}: {exc}"
        raise ConfigError(msg) from exc


def _file_str(data: dict[str, object], key: str, path: Path) -> str | None:
    """Pull a string value out of the parsed config file."""
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        msg = f"{key} in {path} must be a string"
        raise ConfigError(msg)
    return value or None


def generate_app_id() -> str:
    """Build a fresh, unique app_id."""
    return f"{APP_ID_PREFIX}-{secrets.token_hex(_APP_ID_ENTROPY_BYTES)}"


def _fallback_app_id() -> str:
    """Derive a stable app_id from the hostname.

    Used when the config file cannot be written. Being stable matters more than
    being random: a rotating app_id leaves an orphaned endpoint on the
    thermostat every time the process exits without disconnecting cleanly.
    """
    digest = hashlib.sha256(socket.gethostname().encode()).hexdigest()
    return f"{APP_ID_PREFIX}-{digest[: _APP_ID_ENTROPY_BYTES * 2]}"


def _insert_top_level(text: str, line: str) -> str:
    """Add a top-level key, before any table header so the file stays valid."""
    lines = text.splitlines()
    for index, existing in enumerate(lines):
        if existing.lstrip().startswith("["):
            lines.insert(index, line)
            break
    else:
        lines.append(line)
    return "\n".join(lines) + "\n"


def _persist_app_id(path: Path, app_id: str) -> bool:
    """Write the generated app_id back to the config file.

    Returns False when the file could not be written, which is not fatal - the
    caller falls back to a hostname derived id that is stable for this machine.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
        path.write_text(
            _insert_top_level(existing, f'app_id = "{app_id}"'),
            encoding="utf-8",
        )
    except OSError:
        return False
    return True


def _validate_app_id(app_id: str) -> str:
    """Reject an app_id that would not survive being put in a URL path."""
    if not _APP_ID_RE.match(app_id):
        msg = (
            f"Invalid app_id [{app_id}]. It becomes a path segment in the "
            "thermostat's URLs, so use letters, digits, dots, dashes and "
            "underscores only."
        )
        raise ConfigError(msg)
    return app_id


def resolve(
    host: str | None = None,
    app_id: str | None = None,
    *,
    env: dict[str, str] | None = None,
    path: Path | None = None,
    persist: bool = True,
) -> Settings:
    """Resolve settings from the flags, then the environment, then the file.

    When no app_id is configured anywhere, one is generated and written back to
    the config file so it stays the same on every later run. See
    :data:`APP_ID_PREFIX` for why it has to be both unique and stable.

    Args:
        host: The ``--host`` flag, if given.
        app_id: The ``--app-id`` flag, if given.
        env: Environment mapping to read; defaults to ``os.environ``.
        path: Config file to read; defaults to :func:`config_path`.
        persist: Write a generated app_id back to the config file.

    Raises:
        ConfigError: If no host could be found, or the app_id is unusable.
    """
    environ = os.environ if env is None else env
    config_file = config_path() if path is None else path
    data = _load_file(config_file)

    resolved_host = (
        host or environ.get(ENV_HOST) or _file_str(data, "host", config_file)
    )
    if not resolved_host:
        msg = (
            "No thermostat host configured. Pass --host, set "
            f'{ENV_HOST}, or add host = "..." to {config_file}'
        )
        raise ConfigError(msg)

    resolved_app_id = (
        app_id or environ.get(ENV_APP_ID) or _file_str(data, "app_id", config_file)
    )
    if resolved_app_id is None:
        resolved_app_id = generate_app_id()
        if not persist or not _persist_app_id(config_file, resolved_app_id):
            resolved_app_id = _fallback_app_id()

    return Settings(host=resolved_host, app_id=_validate_app_id(resolved_app_id))
