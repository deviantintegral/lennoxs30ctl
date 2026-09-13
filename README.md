# lennoxs30ctl

[![CI](https://github.com/deviantintegral/lennoxs30ctl/actions/workflows/ci.yml/badge.svg)](https://github.com/deviantintegral/lennoxs30ctl/actions/workflows/ci.yml)

A CLI and terminal dashboard for Lennox S30, E30 and M30 thermostats, talking to
the thermostat over its local API. Built on
[lennoxs30api](https://github.com/PeteRager/lennoxs30api).

> 🏠 Looking for a Home Assistant integration? Use
> [lennoxs30](https://github.com/PeteRager/lennoxs30).

## ⚠️ Every client needs its own app_id

The `app_id` is not a nickname, it is the client's address on the thermostat. It
is the path segment in `/Endpoints/{app_id}/Connect` and
`/Messages/{app_id}/Retrieve`, so each one is a separate endpoint with its own
message queue. Two clients sharing an `app_id` will steal each other's messages.

This tool generates its own on first run and writes it to the config file, so it
cannot collide with the Home Assistant integration's `homeassistant`, or with a
copy of this tool on another machine.

Whether the thermostat is happy with two clients connected at once is not
something we can confirm - nobody appears to have tried it and reported back. If
you hit trouble, stop the Home Assistant integration and try again, and please
open an issue either way.

The **S40** has a known firmware bug where a Disconnect leaves the panel unable
to accept any further connection until it is power cycled
([lennoxs30#246](https://github.com/PeteRager/lennoxs30/issues/246)); the library
works around it by never disconnecting from an S40. The S30 is unaffected, and
was tested by the library's maintainer.

## Installation

```bash
uv add lennoxs30ctl
```

To include the terminal dashboard:

```bash
uv add 'lennoxs30ctl[tui]'
```

### Quick run (no install)

```bash
uv tool run 'lennoxs30ctl[tui]'
```

## Configuration

Only local connections are supported, so there are no credentials to store - just
the hostname or IP of the thermostat. Settings are read from, in order:

1. `--host` and `--app-id` on the command line
2. the `LENNOXS30_HOST` and `LENNOXS30_APP_ID` environment variables
3. `~/.config/lennoxs30ctl/config.toml` (or `$XDG_CONFIG_HOME`)

```toml
host = "thermostat.lan"
app_id = "lennoxs30ctl-a1b2c3d4"
```

The `app_id` is generated on first run and written back to that file. It stays
put after that, deliberately: a rotating id would leave an orphaned endpoint on
the thermostat every time the process exits without disconnecting cleanly, which
for a command line tool is often. If the config file cannot be written, one is
derived from the hostname instead, which is stable for the same reason.

## CLI

Add `-v` to any command for verbose logging, or `--debug` to also dump the raw
messages exchanged with the thermostat.

### List systems and zones

```bash
lennoxs30ctl list
```

### Show status

```bash
lennoxs30ctl status            # the system and every active zone
lennoxs30ctl status 0          # one zone, by index
lennoxs30ctl status "Zone 1"   # or by name
```

### Change something

Temperatures are in whichever unit the thermostat itself is set to, so they match
what the panel shows. After a write the tool reads for a few seconds and prints
the zone back, so what you see is the thermostat's state rather than what you
asked for.

```bash
lennoxs30ctl set 0 heat 19            # heat setpoint
lennoxs30ctl set 0 cool 24            # cool setpoint
lennoxs30ctl set 0 setpoint 21        # single setpoint systems
lennoxs30ctl set 0 hvac-mode heat     # off, cool, heat, "heat and cool"
lennoxs30ctl set 0 fan-mode circulate # auto, on, circulate
lennoxs30ctl set 0 humidity-mode off  # off, humidify, dehumidify, both
lennoxs30ctl set 0 humidify 40        # percent
lennoxs30ctl set 0 dehumidify 55      # percent
lennoxs30ctl set 0 preset summer      # a schedule name, or "manual"
lennoxs30ctl set 0 hold off           # cancel a schedule hold
lennoxs30ctl set 0 away on            # manual away mode, system wide
```

### Launch the dashboard

```bash
lennoxs30ctl tui
```

Running `lennoxs30ctl` with no arguments does the same thing.

## TUI

The dashboard updates live. Unlike a cloud API there is nothing to poll: the
local connection is a long-poll message pump, so the display moves as the
thermostat reports changes. `r` is there to re-establish the subscription if the
connection drops, not to fetch.

| Key | Action |
|-----|--------|
| `h` | Heat setpoint |
| `c` | Cool setpoint |
| `t` | Single setpoint |
| `m` | HVAC mode |
| `f` | Fan mode |
| `d` | Humidity mode |
| `u` | Humidify setpoint |
| `e` | Dehumidify setpoint |
| `p` | Schedule |
| `a` | Toggle away mode |
| `o` | Cancel schedule hold |
| `z` | Switch zone |
| `r` | Reconnect |
| `?` | Toggle help overlay |
| `q` | Quit |
| `ctrl+p` | Command palette |

Every action is in the command palette too, so nothing is only reachable by
memorising a key. Displayed values are clickable and open the dialog that edits
them.

## Not yet supported

- **Schedule editing.** Browsing and editing the 28 periods of a schedule is the
  reason this project exists, but it needs `set_schedule_period`, which is not in
  a released `lennoxs30api` yet. It lands here once it is.
- **Cloud connections.** Local only, for now.
- **Equipment parameters and diagnostics.**

## Contributing

```bash
git clone https://github.com/deviantintegral/lennoxs30ctl.git
cd lennoxs30ctl
./scripts/setup.sh

uv run ruff check .
uv run mypy src/
uv run pytest
```

Please follow [Conventional Commits](https://www.conventionalcommits.org/) for
commit messages.

## License

Apache-2.0
