# PitPat T01 — firmware 37 ("fba0") variant

Upstream targets the **T01 (BA04)** revision. This variant speaks the same
application protocol over different GATT characteristics and without the
4-byte transport wrapper. Confirmed on real hardware: firmware `37`,
`device_type` 5, max speed 6000, imperial units.

## Do I have this variant?

```
svc 0000fba0-…      ← vendor service (this variant, vs. ff00-family upstream)
    0000fba2-…  read, notify      ← status stream
    0000fba1-…  read, write       ← commands
```

Enumerate a device's own services/characteristics with:

```python
import asyncio
from bleak import BleakClient
async def m():
    async with BleakClient("AA:BB:CC:DD:EE:FF") as c:
        for s in c.services:
            print("svc", s.uuid)
            for ch in s.characteristics:
                print("   ", ch.uuid, ch.properties)
asyncio.run(m())
```

## Deviations from upstream

| | Upstream (BA04) | This variant |
| --- | --- | --- |
| notify char | `0000ff02-…` | `0000fba2-…` |
| write char  | `0000ff01-…` | `0000fba1-…` |
| inbound wrapper | strips `data[4:]` | none — parse raw |
| outbound wrapper | `4d00 <counter> <length>` + packet | none — write the bare packet |

A wrapped write on this variant is accepted at the ATT layer with no error and
then silently discarded — the belt never moves and nothing looks wrong.
Heartbeat payload is `6a05fdf843` (upstream's leading `05` was the wrapper's
length byte, not part of the packet).

**Unit bit must match the device.** Derive it from the last status frame, not
an assumption of metric: `packet[12] = cmd & 0xF7 if is_kph else cmd | 0x08`.

**Speed/distance are metric on the wire regardless of the unit bit or the
treadmill's own display.** `target_speed` is thousandths of a kph, `distance`
is metres, both directions. Convert at the UI boundary
(`Status.to_wire`/`Status.to_display` in `pitpat/protocol.py`), not in the packet.

`pitpat/protocol.py` describes these two revisions in the `VARIANTS` dict,
keyed `"fba0"` / `"ba04"`. On connect, `pitpat/ble.py` picks the first entry
whose write and notify characteristics the device exposes, and the result is
saved with the address so later connects skip the guess. `--variant` forces one.

### Adapting this for a third variant

To support hardware that differs from both entries in `VARIANTS`, hand a
coding agent this file plus:

1. `pitpat/protocol.py` — add an entry to `VARIANTS` with the new
   `notify_uuid`/`write_uuid` and whether it's `wrapped` (needs the
   `4d00 <counter> <length>` transport header both ways). Detection and
   `--variant` both read from this dict, so nothing else needs to change.
   If the vendor service differs too, add it to `VENDOR_SERVICES` in
   `pitpat/ble.py` so scans find it.
2. Enumerate the device's GATT services/characteristics (the `bleak` snippet
   above) and diff against the tables in this doc to find what differs.
3. Confirm with the XOR checksum: `Status.parse()` returns a status for a
   correctly-unwrapped frame and `None` for one with a wrong offset or a
   leftover wrapper byte, so there's no guessing. `pitpat --verbose` logs
   every raw frame.

## Status frame (notify on `fba2`, 53 bytes, ~1/s, unprompted)

Same layout as BA04 (see `Status.parse`). Notable offsets:

| Offset | Field |
| --- | --- |
| 0 | frame type — `0x66` here, `0x68` on BA04 |
| 3–4 / 5–6 | current / target speed, BE |
| 26 | flags: bit 0 connected, bits 3–4 running state, bit 7 unit mode (1=imperial) |
| 51 | XOR checksum over bytes 1–50 |

### Is someone on the belt? (bytes 32 and 40)

In the regular 50-byte frame (not the occasional 52-byte identity frame, where
bytes 32-47 are the serial number), two bytes follow whether a person is standing
on the belt. Found with `tools/presence_watch.py` on the firmware-37 T01, while the
belt ran at 1 mph and the rider alternated between standing on it and stepping onto
the side rails, three times:

| Byte | Meaning | Observed |
| --- | --- | --- |
| 40 | load (instant) | 10-12 with nobody on it (6-14 over other runs), 34-70 walking on it. Reacts within about 2 s of stepping off or on |
| 32 | "off the belt" flag | 0 while on. Became 1 about 10-11 s after stepping off, and went back to 0 within about 2 s of stepping back on |

Nothing is reported while the belt is stopped: every byte was constant. The app
treats a load under 20 or the flag set as "off" (`Status.on_belt`), so it can act
sooner than the treadmill's own ~10 s flag, with the flag as a backstop.

Caveats: one person, one treadmill, one speed (and 0 incline). The load is likely
motor current, so an empty belt may read higher at speed or on an incline; if it
rises above 20 the flag still catches it, just later. The BA04 revision's frames
were not tested and don't carry these bytes in the layout we parse.

## Command frame (write to `fba1`, 23 bytes, sent bare)

| Command | Packet |
| --- | --- |
| start 1.00 | `6a17 00000000 03e8 01 00 50 00 0c <userid> 12 43` |
| set 1.10 | `6a17 00000000 044c 05 00 50 00 0c <userid> b5 43` |
| pause | `6a17 00000000 0000 01 00 50 00 0a <userid> ff 43` |
| stop | `6a17 00000000 0000 01 00 50 00 08 <userid> fd 43` |

Byte 12 is the command (0 stop, 2 pause, 4 start/set, `|0x08` imperial).
User id is unvalidated — the hardcoded `USER_ID` works fine,
no pairing/bonding required. Heartbeat/status-poll is the same shape at
length 5: `6a 05 fd f8 43`.

### Incline is byte 9

In the table above byte 9 is the `00` after the ramp byte. It is the **incline
level**, and a start/set-speed command sets the treadmill to it. Found with
`tools/incline_probe.py` on the firmware-37 T01 (max incline 12):

- Sending the belt's own speed with byte 9 = 1, 2, 3 moved both the reported
  incline (status byte 11) and target (byte 12) to 1, 2, 3, one for one, the same
  levels the remote and phone app use.
- It is **absolute**. Sending 0 afterwards took the incline back to 0. So any
  start/set-speed packet with byte 9 left at 0 flattens a raised treadmill. The
  app therefore sends the treadmill's own current incline unless it is changing it.
- It only takes effect while the belt is running.
- Pause and stop packets were not tested with a non-zero byte 9 and the app
  leaves theirs at 0.
- Only 0–3 was tried, and only on this model. The app clamps to the max the
  treadmill reports (status byte 29).

## Troubleshooting

**`Device with address … was not found`** — usually a stale link, not a
missing device: something else (desktop Bluetooth panel, a previous session)
already holds the connection, so it stops advertising. Check with
`bluetoothctl info <MAC> | grep Connected`; fix with
`bluetoothctl disconnect <MAC>`. `_release_stale_link()` in
`pitpat/ble.py` does this automatically before each connect (Linux only).
Never connect the treadmill from the desktop Bluetooth panel — let the app
own the link. Pairing is neither needed nor wanted.

**Connects, but the speed stays at `--`** — wrong notify UUID, or the `data[4:]`
strip is still present.

**Connects and shows data, buttons do nothing** — outbound wrapper still
present; check logged bytes start with `6a`, not `4d00`.

**`2b10`/`2b11` (service `1910`)** — advertised but inert on this firmware,
not a control channel.
