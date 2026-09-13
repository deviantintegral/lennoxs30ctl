"""Tests for settings resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from lennoxs30ctl.config import (
    DEFAULT_APP_ID,
    ConfigError,
    config_path,
    resolve,
)


class TestPrecedence:
    """Flags beat the environment, which beats the config file."""

    def test_flag_wins(self, tmp_path: Path) -> None:
        config = tmp_path / "config.toml"
        config.write_text('host = "from-file"\napp_id = "file-id"\n')
        settings = resolve(
            "from-flag",
            "flag-id",
            env={"LENNOXS30_HOST": "from-env", "LENNOXS30_APP_ID": "env-id"},
            path=config,
        )
        assert settings.host == "from-flag"
        assert settings.app_id == "flag-id"

    def test_env_beats_file(self, tmp_path: Path) -> None:
        config = tmp_path / "config.toml"
        config.write_text('host = "from-file"\n')
        settings = resolve(None, None, env={"LENNOXS30_HOST": "from-env"}, path=config)
        assert settings.host == "from-env"

    def test_file_used_last(self, tmp_path: Path) -> None:
        config = tmp_path / "config.toml"
        config.write_text('host = "from-file"\napp_id = "file-id"\n')
        settings = resolve(None, None, env={}, path=config)
        assert settings.host == "from-file"
        assert settings.app_id == "file-id"

    def test_app_id_defaults(self, tmp_path: Path) -> None:
        settings = resolve("host", None, env={}, path=tmp_path / "missing.toml")
        assert settings.app_id == DEFAULT_APP_ID

    def test_default_app_id_differs_from_home_assistant(self) -> None:
        """Sharing an app_id with the integration breaks both clients."""
        assert DEFAULT_APP_ID != "homeassistant"


class TestErrors:
    """Failures are reported rather than guessed around."""

    def test_missing_host(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="No thermostat host"):
            resolve(None, None, env={}, path=tmp_path / "missing.toml")

    def test_unparseable_file(self, tmp_path: Path) -> None:
        config = tmp_path / "config.toml"
        config.write_text("this is not = = toml\n")
        with pytest.raises(ConfigError, match="Could not read"):
            resolve(None, None, env={}, path=config)

    def test_wrong_type_in_file(self, tmp_path: Path) -> None:
        config = tmp_path / "config.toml"
        config.write_text("host = 42\n")
        with pytest.raises(ConfigError, match="must be a string"):
            resolve(None, None, env={}, path=config)

    def test_empty_string_in_file_is_ignored(self, tmp_path: Path) -> None:
        config = tmp_path / "config.toml"
        config.write_text('host = ""\n')
        with pytest.raises(ConfigError):
            resolve(None, None, env={}, path=config)


class TestConfigPath:
    """The config file location honours XDG_CONFIG_HOME."""

    def test_respects_xdg(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/xdg")
        assert config_path() == Path("/tmp/xdg/lennoxs30ctl/config.toml")

    def test_falls_back_to_home(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        assert config_path() == Path.home() / ".config/lennoxs30ctl/config.toml"

    def test_default_path_is_used(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Calling resolve with no path reads the real config location."""
        monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/definitely-not-here")
        settings = resolve("host", None, env={})
        assert settings.host == "host"
