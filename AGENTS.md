# Agents

lennoxs30ctl is a CLI and TUI for Lennox S30/E30/M30 thermostats. It talks to
the thermostat over the local API using the
[lennoxs30api](https://github.com/PeteRager/lennoxs30api) library.

## Package Management

This project uses [`uv`](https://docs.astral.sh/uv/) for package management. Do **not** use `pip` directly.

Install dependencies with:

```bash
uv sync --dev --all-extras
```

After syncing, install pre-commit hooks:

```bash
uv run pre-commit install --install-hooks
```

Add packages with:

```bash
uv add <package>
```

## Pull Requests

Use [conventional commit](https://www.conventionalcommits.org/) formatting for pull request titles and descriptions. The pull request title and description should typically match the message and description of the first commit.

## Development

```bash
# Lint and type-check
uv run ruff check .
uv run mypy src/

# Run tests
uv run pytest
```

## Architecture

- The library is async-first. All network work goes through `lennoxs30api`.
- Only local (LAN) connections are supported. There is no cloud login, no
  credential storage and no token cache.
- `connection.py` owns the connection lifecycle: connect, subscribe, and a
  background task that loops on `messagePump()`. On a LAN connection that call
  long-polls for 15 seconds and returns `False` when nothing arrived, so the
  loop needs no sleep of its own.
- State lives on the `lennox_system` and `lennox_zone` objects and is mutated by
  the pump. Update callbacks registered with `registerOnUpdateCallback` fire
  from inside that task, so they must only mark state dirty and schedule a
  redraw. Never render from a callback.
- `lennoxs30api` ships no `py.typed`, so everything it exports is `Any`.
  `connection.py` and `format.py` are the only modules allowed to import it;
  they hand typed values to everything else. Keep it that way or `mypy strict`
  becomes strict in name only.
- The optional TUI is built with the `textual` framework (installed via the `tui` extra).
- Strict `mypy` type checking is enforced across the entire `src/` directory.
- Test coverage must remain at or above 95%.
- New code must include corresponding tests.

## Only one client at a time

The LCC does not cope well with several clients subscribing at once. If you are
running the Home Assistant integration against the same thermostat, stop it
before using this tool. The default `app_id` is `lennoxs30ctl` so it can never
collide with the integration's, but that is not a substitute for stopping it.

## Test fixtures

`tests/fixtures/*.json` are real captured messages copied from the
`lennoxs30api` test suite, which is MIT licensed. They describe a four zone LAN
system. Keep them as they arrived - they are the ground truth for the message
shapes this tool parses.
