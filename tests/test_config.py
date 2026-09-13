"""Tests for settings resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from lennoxs30ctl.config import (
    APP_ID_PREFIX,
    ConfigError,
    config_path,
    generate_app_id,
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

    def test_generated_when_absent(self, tmp_path: Path) -> None:
        settings = resolve("host", None, env={}, path=tmp_path / "config.toml")
        assert settings.app_id.startswith(f"{APP_ID_PREFIX}-")

    def test_generated_app_id_differs_from_home_assistant(self, tmp_path: Path) -> None:
        """Sharing an app_id with the integration breaks both clients."""
        settings = resolve("host", None, env={}, path=tmp_path / "config.toml")
        assert settings.app_id != "homeassistant"


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

    def test_default_path_is_used(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Calling resolve with no path reads the real config location."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        settings = resolve("host", None, env={})
        assert settings.host == "host"
        assert (tmp_path / "lennoxs30ctl" / "config.toml").is_file()


class TestGeneratedAppId:
    """A generated app_id is written back so it stays the same next time."""

    def test_persisted_to_a_new_file(self, tmp_path: Path) -> None:
        config = tmp_path / "sub" / "config.toml"
        first = resolve("host", None, env={}, path=config)
        assert config.is_file()
        assert first.app_id in config.read_text()

    def test_stable_across_runs(self, tmp_path: Path) -> None:
        """A rotating id would orphan an endpoint on every unclean exit."""
        config = tmp_path / "config.toml"
        first = resolve("host", None, env={}, path=config)
        second = resolve("host", None, env={}, path=config)
        assert first.app_id == second.app_id

    def test_existing_settings_are_kept(self, tmp_path: Path) -> None:
        config = tmp_path / "config.toml"
        config.write_text('host = "from-file"\n')
        settings = resolve(None, None, env={}, path=config)
        text = config.read_text()
        assert 'host = "from-file"' in text
        assert settings.app_id in text
        assert resolve(None, None, env={}, path=config).host == "from-file"

    def test_inserted_before_any_table(self, tmp_path: Path) -> None:
        """A bare key after a table header would land inside that table."""
        config = tmp_path / "config.toml"
        config.write_text('host = "h"\n\n[logging]\nlevel = "debug"\n')
        settings = resolve(None, None, env={}, path=config)
        reread = resolve(None, None, env={}, path=config)
        assert reread.app_id == settings.app_id

    def test_unwritable_file_falls_back_to_a_stable_id(self, tmp_path: Path) -> None:
        config = tmp_path / "config.toml"
        first = resolve("host", None, env={}, path=config, persist=False)
        second = resolve("host", None, env={}, path=config, persist=False)
        assert first.app_id == second.app_id
        assert first.app_id.startswith(f"{APP_ID_PREFIX}-")
        assert not config.exists()

    def test_write_failure_falls_back(self, tmp_path: Path) -> None:
        """The parent is a file, so creating the config directory fails."""
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory\n")
        settings = resolve("host", None, env={}, path=blocker / "config.toml")
        assert settings.app_id.startswith(f"{APP_ID_PREFIX}-")
        assert blocker.read_text() == "not a directory\n"

    def test_generate_is_random(self) -> None:
        assert generate_app_id() != generate_app_id()

    def test_generated_ids_are_url_safe(self) -> None:
        app_id = generate_app_id()
        assert app_id.replace("-", "").isalnum()
        assert len(app_id) < 28


class TestAppIdValidation:
    """The app_id becomes a path segment, so it cannot contain anything odd."""

    def test_rejects_a_slash(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="Invalid app_id"):
            resolve("host", "bad/id", env={}, path=tmp_path / "c.toml")

    def test_rejects_a_space(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="Invalid app_id"):
            resolve("host", "bad id", env={}, path=tmp_path / "c.toml")

    def test_accepts_the_home_assistant_style(self, tmp_path: Path) -> None:
        settings = resolve("host", "ha_prod-1.2", env={}, path=tmp_path / "c.toml")
        assert settings.app_id == "ha_prod-1.2"
