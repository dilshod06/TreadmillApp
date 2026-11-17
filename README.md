# PitPat Treadmill Control

![Screenshot of the PitPat terminal UI](assets/screenshot.png)

An unofficial terminal app to control and monitor a PitPat treadmill over Bluetooth.
It remembers your treadmill, so getting walking is one command:

```bash
pitpat 2.5      # connect to the last treadmill and start the belt at 2.5
```

---

## Install

Needs **Python 3.10+** and a Bluetooth adapter. Works on Linux and Windows.

The easiest way is [uv](https://docs.astral.sh/uv/), which installs `pitpat` into its
own environment and puts the command on your PATH:

```bash
uv tool install git+https://github.com/jamers99/pitpat-treadmill-control.git
```

Get uv with `pacman -S uv`, `brew install uv`, `winget install astral-sh.uv`, or
[the installer](https://docs.astral.sh/uv/getting-started/installation/).
`pipx install git+https://…` works the same way.

From a clone, `uv tool install -e .` runs the checkout directly, so your edits
take effect without reinstalling. To update, run `uv tool upgrade pitpat`; to
remove, `uv tool uninstall pitpat`.

Then run `pitpat` (or `python -m pitpat`).

## Desktop app and tray icon

There's also a windowed version that lives in the system tray. It needs the
`gui` extra (PySide6):

```bash
uv tool install "pitpat[gui] @ git+https://github.com/jamers99/pitpat-treadmill-control.git"
pitpat-gui              # open the window
pitpat-gui --minimized  # start in the tray only
```

From a clone: `uv tool install -e ".[gui]"`.

- **Window:** live speed, time, distance, steps and calories, with the same
  controls as the terminal app. Space, ↑ ↓ and 1–9 work as shortcuts, and
  right-clicking a preset saves the current speed there.
- **Tray icon:** shows the current speed and turns green, amber or grey with the
  belt's state. Hover for time and distance. Click to show or hide the window;
  right-click for start/pause, speed ±0.1, presets, stop and quit.
- **Closing the window** keeps it running in the tray. Quit from the tray menu,
  which pauses the belt first if it's moving.
- **Start with Windows** (on the Settings page) launches it in the tray at login. It
  retries quietly until your treadmill is on.
- Only one copy runs at a time; launching it again brings up the open window.

### Modes: Normal and Countdown

The window has three tabs under the connection line (Normal, Countdown and **History**, below),
and a gear at the right for **Settings**.
The tabs only change what you see: a plan keeps running when you look at another one, the
Countdown tab shows its progress ("Countdown · 40%"), and Normal shows a bar you can click
to get back to it.

- **Normal** is the manual control described above: you set the speed.
- **Countdown** follows a plan. Each plan has a length, either a **distance**
  (say 2 miles) or a **time** (say 30 minutes), and a list of **points** that say
  where along it the speed and incline change. The first point is the start, so a
  plan of "3 miles at 3 mph, incline 1" is a single point; adding more points
  makes it variable ("at 0.5 mi go to 3.5 mph, incline 2; at 1.5 mi ease to 2.5").
  Make as many plans as you like with **New plan…**, then press **▶ Run** on one
  (or pick it from the tray's **Plans** menu). It starts the belt if it's stopped
  and goes.

While a plan runs you can still use the − / + buttons, the presets or an exact
speed. Your speed holds until the plan reaches its next point, which then takes
over again. **Skip to next point** jumps ahead, and **End plan** hands control back with the belt carrying on. Pressing **Stop** ends the
plan too. At the end the plan stops the belt, unless you turned that off for it.
If the connection drops the plan ends, since the treadmill's counters can't be
trusted across a reconnect.

Progress comes from the treadmill's own distance and time counters, so pausing
pauses the plan. During a plan the window's **Distance** card (or **Time**, for a time
plan) counts down from the plan's length to 0 instead of up; it is the app's display, and
the treadmill's own screen is unchanged. Right-click a plan to duplicate or delete it. Plans are kept in
the same `config.json` as your presets.

**Incline.** The treadmill takes its incline from the same command as the speed,
and the app can send it, but this is **off until you tick "Let the app set the
incline"** on the Settings tab. The belt must be running for the
incline to move.

- **Off (the default):** the app never changes the incline. A plan tells you
  what to set at each point, and the run panel compares the target with what
  the treadmill reports.
- **On:** a plan sets the incline at each point, and the window (and tray) get
  incline − / + buttons. Like speed, an incline you change by hand holds until
  the plan's next point.

Either way, speed changes made in the app keep whatever incline the treadmill is
at, so using the app doesn't flatten an incline you set with the remote.

### Pause when you step off

The treadmill can tell whether someone is on the belt. Tick **Pause the belt if
I'm off it for N seconds** on the Settings tab and set the seconds (3 to 600). It's off
by default.

- It pauses the belt once you've been off it for that long. Pausing is the same
  as pressing Pause, so a Countdown plan waits too, and Start carries on.
- It only starts watching once you've been on the belt since it started, so you can
  start the belt and then step on.
- The window shows "On the belt", or "Off the belt · pausing in N s".
- It reads the treadmill's load sensor, which reacts in about two seconds, so very
  short settings can pause on a stumble. Tested on one PitPat T01 at walking speed
  only, so try it at your own speeds before relying on it. It does nothing on a
  treadmill that doesn't report it, and says so.

### Rest

The manual asks for a rest after a long run. On the Settings tab, **Make the treadmill
rest** is on by default: after **120 minutes** of the belt running it is paused, you are
told it needs to cool down, and the app won't start it again until it has rested
**15 minutes**. Both numbers are yours to set (10 to 600 minutes of running, 1 to 120 of rest).

- A rest has to be the full length: a shorter pause doesn't reset the clock.
- The window shows "Rest in 1:12:00", then "Cooling down · 9:30" once it's due. Starting
  from the remote during a rest is paused again.
- The running time is counted by the app while it is open, so quitting it forgets the
  count. Untick the setting to turn the limit off.

### Floors and workout history

**Floors climbed** shows live next to the distance, and on the plan screen. It is the
distance you've covered times the incline, as a grade, at 10 ft a floor. The treadmill
doesn't report floors, and the app takes incline level N to be an N % grade, so treat the
number as an estimate.

Every workout is saved to the **History** tab, by day. A workout runs from when the belt
first moves until the treadmill stops, whether you pressed Stop, a plan finished, or you
used the remote. Pausing doesn't end it, and quitting or losing the connection saves
what was done so far. Under about 0.01 mile isn't saved. Pick **This week**, **Last
week**, **Last 4 weeks** or **Pick dates** to see distance, floors, workouts and time, a
chart per day (per week for long ranges), a week-by-week table and the daily records.
**Generate summary** makes a short text with the totals for those dates to copy. Weeks
run Monday to Sunday, and distance is in the treadmill's own units.

### Settings page

Incline, stepping off, rest, starting with Windows and choosing a treadmill are all here,
and the treadmill's details under **Details**. The tray menu has **Settings…** to open it.

It shares its saved treadmill and presets with the terminal app, but run only one
of them at a time, since a treadmill accepts a single Bluetooth connection.

## Usage

```
pitpat [SPEED] [--scan] [--address ADDR] [--variant {fba0,ba04}] [--verbose]
```

- **First run:** it scans for treadmills. If it finds exactly one, it connects
  straight away. Otherwise you pick one from the list or type its address.
- **After that:** it reconnects to the same treadmill right away, without scanning.
  Use `--scan` or press `c` to choose a different one.
- **`SPEED`:** starts the belt at that speed as soon as it's connected.
- **Dropped connection:** it keeps retrying until the treadmill is back.

### Keys

| Key | Action |
| --- | --- |
| `Space` | Start / pause (resumes at the last speed) |
| `↑` `↓` (or `+` `-`) | Speed ±0.1 |
| `1`–`9` | Go to a preset speed, starting the belt if it's stopped |
| `Enter` | Type an exact speed |
| `s` | Stop (pauses first if the belt is running) |
| `p` then `1`–`9` | Save the current speed as that preset |
| `d` | Show or hide details (incline, firmware, serial, …) |
| `c` | Choose a different treadmill |
| `q` / `Ctrl+C` | Quit (pauses the belt first if it's running) |

Speeds use whatever unit the treadmill's own display uses (km/h or mph), and are
capped at its maximum. Presets above the maximum are dimmed.

### Settings and logs

The last treadmill, your presets, plans and settings are kept in `config.json`, your workouts in
`history.json` (one entry per workout, written safely so a crash can't corrupt it; copy it to back
it up), and logs in `pitpat.log`, all in:

- Linux: `~/.config/pitpat/`
- Windows: `%APPDATA%\pitpat\`

You can edit `presets` in the config by hand. It holds up to nine speeds.

---

## Compatible devices

- PitPat T01 (BA04)
- PitPat T01, firmware 37 (`fba0` variant). See [docs/FIRMWARE-VARIANT.md](docs/FIRMWARE-VARIANT.md)

The variant is detected automatically when connecting. To report another
working model, open an [issue](https://github.com/jamers99/pitpat-treadmill-control/issues)
or pull request.

## Troubleshooting

- **Treadmill not found:** don't connect it from your desktop's Bluetooth panel or
  the PitPat phone app. A BLE device stops advertising while something else holds
  it. On Linux, pitpat drops a stale BlueZ link on its own before connecting.
  Pairing isn't needed.
- **Windows:** run it natively, not in WSL2, which has no Bluetooth stack. Clearing a
  stale link is Linux-only, so if the scan comes up empty, close other apps using
  the treadmill or turn it off and on again.
- Run with `--verbose` to log every status frame to `pitpat.log`.

## Development

```bash
uv run --extra test pytest
```

| File | What it does |
| --- | --- |
| `pitpat/protocol.py` | Command packets and status-frame decoding |
| `pitpat/ble.py` | Scanning and the Bluetooth connection |
| `pitpat/app.py` | The terminal UI (built with [Textual](https://textual.textualize.io)) |
| `pitpat/config.py` | Saved settings |

---

## License

[MIT](LICENSE)

## Disclaimer

This project is an independent, unofficial tool and is **not affiliated with, endorsed by, or associated with PitPat or JOYFIT INC**. It was developed from scratch for non-commercial, personal experimentation and educational purposes, based solely on publicly observable interactions with PitPat treadmills to enable interoperability. No proprietary code, assets, or intellectual property from PitPat or JOYFIT INC have been used or incorporated. This tool is not intended to bypass security measures or violate any terms of service. This software is provided 'as-is', without any warranties, express or implied, and the author assumes no responsibility for any damage, malfunction, or warranty issues resulting from its use. Use at your own risk and ensure compliance with applicable laws and terms of service.
