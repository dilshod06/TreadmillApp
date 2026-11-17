"""Finds the byte in the status frame that says whether someone is on the belt.

Result: on the PitPat T01 this was written for, byte 40 is an instant load reading
and byte 32 is the treadmill's own "off the belt" flag, which lags by about 10
seconds (see docs/FIRMWARE-VARIANT.md). The built-in report below can miss a
lagging byte, so read the log too. Kept for checking other models and speeds.

Nothing documents it, so this records the treadmill's raw status frames while
you stand ON the belt and then step OFF it, three times, and reports which byte
follows you. It is read-only: it sends no commands, only the usual heartbeat.

Before you run it:
  * Quit the PitPat tray app / terminal app and close the phone app. The treadmill
    takes one Bluetooth connection at a time.
  * Start the belt SLOWLY with the remote (1 km/h is plenty), so the sensor is
    in the state you'll use it in. "Off" means stepping onto the side rails.
    Keep a hand on the rail and near the safety key. Or leave the belt stopped
    and stand on / beside it, which is safer but may not show the sensor.

What it does: waits until the belt is running and you are ON it, then every 15 seconds says ON or OFF
to alternate, six stretches (90 seconds). Move when it tells you to. It keeps
every frame in presence-<time>.log in the current folder and prints the bytes
that changed with you.

    python tools/presence_watch.py [--address ADDR] [--seconds 15] [--grace 4]
"""
import argparse
import asyncio
import logging
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, Optional

from pitpat.config import Config
from pitpat.protocol import State

log = logging.getLogger("presence_watch")

PHASES = ("ON", "OFF") * 3
MAX_DISTINCT = 8  # A byte that follows you takes few values; a counter takes many

# One recorded frame: (stretch number, ON or OFF, seconds into the stretch, raw frame)
Sample = tuple[int, str, float, bytes]


def analyze(samples: list[Sample], grace: float) -> list[dict]:
    """Bytes whose values while ON never occur while OFF, in every stretch alike.

    The first `grace` seconds of each stretch are ignored, since people take a
    moment to move. Frames are compared only with frames of the same length,
    because the treadmill sends more than one kind.
    """
    by_kind: dict[int, list[Sample]] = defaultdict(list)
    for sample in samples:
        if sample[2] >= grace:
            by_kind[len(sample[3])].append(sample)

    found = []
    for size, group in sorted(by_kind.items()):
        labels = {number: label for number, label, _, _ in group}
        if sum(l == "ON" for l in labels.values()) < 2 or sum(l == "OFF" for l in labels.values()) < 2:
            continue  # Not enough stretches to tell a pattern from a coincidence
        for offset in range(size):
            on = {frame[offset] for _, label, _, frame in group if label == "ON"}
            off = {frame[offset] for _, label, _, frame in group if label == "OFF"}
            if on & off or len(on | off) > MAX_DISTINCT:
                continue
            found.append({"size": size, "offset": offset, "on": sorted(on), "off": sorted(off)})
    return found


def report(found: list[dict]) -> list[str]:
    if not found:
        return ["No byte separated ON from OFF cleanly. Send the log file for a closer look."]
    lines = ["Bytes that followed you (frame length, byte number: values while ON | values while OFF):"]
    for hit in found:
        on = ",".join(map(str, hit["on"]))
        off = ",".join(map(str, hit["off"]))
        lines.append(f"  {hit['size']}-byte frame, byte {hit['offset']}: ON {on} | OFF {off}")
    return lines


class Recorder:
    def __init__(
        self,
        make_treadmill: Callable[..., object],
        address: str,
        variant: Optional[str],
        ask: Callable[[str], Awaitable[str]],
        say: Callable[[str], None] = print,
        seconds: float = 15.0,
        grace: float = 4.0,
    ):
        self.make_treadmill = make_treadmill
        self.address = address
        self.variant = variant
        self.ask = ask
        self.say = say
        self.seconds = seconds
        self.grace = grace
        self.treadmill = None
        self.frames = 0
        self.last_status = None
        self.current = (-1, "")  # (stretch number, label); the stretch frames are filed under
        self.started = 0.0
        self.samples: list[Sample] = []

    def _on_raw(self, frame: bytes) -> None:
        self.frames += 1
        number, label = self.current
        if number >= 0:
            now = asyncio.get_running_loop().time() - self.started
            self.samples.append((number, label, now, frame))
        log.debug("frame %s %s", label or "-", frame.hex())

    def _on_status(self, status) -> None:
        self.last_status = status

    def _on_lost(self) -> None:
        self.say("!! Lost the Bluetooth connection.")
        log.warning("connection lost")

    async def connect(self) -> None:
        self.say(f"Connecting to {self.address}…")
        self.treadmill = self.make_treadmill(self.address, self._on_status, self._on_lost, self.variant)
        self.treadmill.on_raw = self._on_raw
        await self.treadmill.connect()
        await self._wait_for_frames(1, timeout=15)
        if not self.frames:
            raise RuntimeError("Connected, but no status arrived.")
        s = self.last_status
        self.say(f"Connected. belt: {s.state.name}, {s.target_speed / 1000:g} km/h" if s else "Connected.")

    @property
    def belt_running(self) -> bool:
        s = self.last_status
        return s is not None and s.state == State.RUNNING and s.target_speed > 0

    async def _wait_for_frames(self, count: int, timeout: float) -> None:
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        start = self.frames
        while self.frames - start < count and loop.time() < end:
            await asyncio.sleep(0.01)

    async def record(self) -> None:
        loop = asyncio.get_running_loop()
        for number, label in enumerate(PHASES):
            self.started = loop.time()
            self.current = (number, label)
            if label == "ON":
                self.say(f"\n[{number + 1}/{len(PHASES)}] Stand ON the belt, walking normally, for {self.seconds:g}s.")
            else:
                self.say(f"\n[{number + 1}/{len(PHASES)}] Step OFF onto the side rails for {self.seconds:g}s. Hold the rail.")
            await asyncio.sleep(self.seconds)
        self.current = (-1, "")

    async def finish(self) -> None:
        if self.treadmill is not None:
            try:
                await self.treadmill.disconnect()
            except Exception as e:
                self.say(f"!! Disconnect problem: {e}")


async def run(rec: Recorder) -> None:
    try:
        await rec.connect()
        prompt = (
            "\nFirst START THE BELT with the remote, slowly (1 km/h). This tool never starts it.\n"
            "Then stand ON the belt and press Enter (or type q to quit): "
        )
        while True:
            if await rec.ask(prompt) == "q":
                return
            await rec._wait_for_frames(2, timeout=5)  # Let a fresh status in
            if rec.belt_running:
                break
            prompt = "\nThe belt isn't running. Start it with the remote, then press Enter (or q to quit): "
        await rec.record()
    finally:
        await rec.finish()
        if rec.samples:  # Nothing to say about a run that never recorded
            for line in report(analyze(rec.samples, rec.grace)):
                rec.say(line)


async def ask_user(prompt: str) -> str:
    return (await asyncio.get_running_loop().run_in_executor(None, input, prompt)).strip().lower()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("-a", "--address", help="treadmill address (default: the last one the app used)")
    parser.add_argument("--seconds", type=float, default=15.0, help="length of each ON/OFF stretch")
    parser.add_argument("--grace", type=float, default=4.0, help="seconds ignored at the start of each stretch")
    args = parser.parse_args()

    config = Config.load()
    address = args.address or config.address
    if not address:
        print("No treadmill saved yet. Pass --address, or connect once with the app.")
        return 2
    variant = config.variant if address.upper() == (config.address or "").upper() else None

    log_file = Path.cwd() / f"presence-{datetime.now():%Y%m%d-%H%M%S}.log"
    logging.basicConfig(
        filename=log_file, level=logging.DEBUG, encoding="utf-8",
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    print(f"Logging to {log_file}")

    from pitpat import ble

    class Raw(ble.Treadmill):
        on_raw: Callable[[bytes], None] = staticmethod(lambda frame: None)

        async def _on_notify(self, char, data):
            self.on_raw(bytes(data[4:] if self.wrapped else data))
            await super()._on_notify(char, data)

    rec = Recorder(Raw, address, variant, ask_user, say=lambda m: (print(m), log.info(m))[0],
                   seconds=args.seconds, grace=args.grace)
    try:
        asyncio.run(run(rec))
    except KeyboardInterrupt:
        print("\nInterrupted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
