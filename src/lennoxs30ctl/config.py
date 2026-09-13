"""Connection settings, resolved from flags, the environment, or a config file."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import NamedTuple

ENV_HOST = "LENNOXS30_HOST"
ENV_APP_ID = "LENNOXS30_APP_ID"

#: Must differ from the Home Assistant integration's app_id, which is
#: "homeassistant". Two clients sharing an app_id fight over the subscription.
DEFAULT_APP_ID = "lennoxs30ctl"


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


def _file_str(data: dict[str, object], key: str) -> str | None:
    """Pull a string value out of the parsed config file."""
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        msg = f"{key} in {config_path()} must be a string"
        raise ConfigError(msg)
    return value or None


def resolve(
    host: str | None = None,
    app_id: str | None = None,
    *,
    env: dict[str, str] | None = None,
    path: Path | None = None,
) -> Settings:
    """Resolve settings from the flags, then the environment, then the file.

    Args:
        host: The ``--host`` flag, if given.
        app_id: The ``--app-id`` flag, if given.
        env: Environment mapping to read; defaults to ``os.environ``.
        path: Config file to read; defaults to :func:`config_path`.

    Raises:
        ConfigError: If no host could be found anywhere.
    """
    environ = os.environ if env is None else env
    data = _load_file(config_path() if path is None else path)

    resolved_host = host or environ.get(ENV_HOST) or _file_str(data, "host")
    if not resolved_host:
        msg = (
            "No thermostat host configured. Pass --host, set "
            f'{ENV_HOST}, or add host = "..." to {config_path()}'
        )
        raise ConfigError(msg)

    resolved_app_id = (
        app_id or environ.get(ENV_APP_ID) or _file_str(data, "app_id") or DEFAULT_APP_ID
    )
    return Settings(host=resolved_host, app_id=resolved_app_id)
