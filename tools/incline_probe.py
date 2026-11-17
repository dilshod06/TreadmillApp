"""Finds out whether byte 9 of the command frame sets a PitPat's incline.

Result: it does. On the PitPat T01 this was written for, byte 9 is the incline
level and the reported incline follows it (see docs/FIRMWARE-VARIANT.md). The
script is kept to check other models, or other firmware, before trusting that.

Nobody had documented an incline command for these treadmills. One other
project's packet builder has an incline byte at position 9 that it never sets.
This script sends small values there and watches what the treadmill reports.

The incline only works while the belt is running, so YOU start the belt, slowly,
with the remote. The script never starts it and never changes its speed: it
re-sends the speed the belt already has, with byte 9 set.

Before you run it:
  * Quit the PitPat tray app / terminal app and close the phone app. The treadmill
    takes one Bluetooth connection at a time.
  * Set the incline to 0 with the remote, and keep the deck clear.
  * Stand beside the belt, or walk at a very slow speed with a hand on the rail.
    Keep a hand near the treadmill's safety key.

What it does:
  1. Connects, then waits while you start the belt and set the incline to 0.
  2. Reads only for a few seconds while you nudge the incline with the remote, up
     a few steps and back. This shows how the reported numbers scale.
  3. Only if you type `go belt`: sends byte 9 = 1, 2, then 3 (never more), a few
     seconds apiece, then puts the incline byte back. It checks whether the
     reported incline or target follows. Any keypress (Windows) or Ctrl+C pauses
     the belt at once.

The belt is left running, as you had it, unless you abort or something fails, in
which case it is paused. Everything is logged, including raw status frames, to
incline-probe-<time>.log in the current folder. Send that file back.

    python tools/incline_probe.py [--address ADDR] [--observe 15]
"""
import argparse
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, Optional

from pitpat import protocol
from pitpat.config import Config
from pitpat.protocol import Command, State, Status

log = logging.getLogger("incline_probe")

PROBE_VALUES = (1, 2, 3)  # Never send more than this
MAX_START_INCLINE = max(PROBE_VALUES)  # Refuse to start if it is already higher
FRAMES_PER_SEND = 4  # The treadmill acts on a command at its next status frame; repeat to be sure
SETTLE_SECONDS = 7.0  # Time for an incline motor to move


def reading(s: Status) -> str:
    return f"incline={s.incline} target={s.target_incline} max={s.max_incline} speed={s.target_speed / 1000:g}km/h state={s.state.name}"


class Probe:
    def __init__(
        self,
        make_treadmill: Callable[..., object],
        address: str,
        variant: Optional[str],
        ask: Callable[[str], Awaitable[str]],
        key_pressed: Callable[[], bool],
        say: Callable[[str], None] = print,
        settle: float = SETTLE_SECONDS,
    ):
        self.make_treadmill = make_treadmill
        self.address = address
        self.variant = variant
        self.ask = ask
        self.key_pressed = key_pressed
        self.say = say
        self.settle = settle
        self.treadmill = None
        self.status: Optional[Status] = None
        self.frames = 0
        self.hold_speed = 0  # The belt's own speed, re-sent unchanged
        self.restore_incline = 0
        self.touched_incline = False  # We've sent a non-zero byte 9 and owe a reset
        self.aborted = False  # A keypress or a failure: pause the belt on the way out
        self.results: list[tuple[int, int, int]] = []  # (sent, reported incline, reported target)

    # --- Connection -------------------------------------------------------------

    def _on_status(self, status: Status) -> None:
        self.status = status
        self.frames += 1

    def _on_lost(self) -> None:
        self.say("!! Lost the Bluetooth connection.")
        log.warning("connection lost")

    async def connect(self) -> None:
        self.say(f"Connecting to {self.address}…")
        self.treadmill = self.make_treadmill(self.address, self._on_status, self._on_lost, self.variant)
        await self.treadmill.connect()
        await self.next_frame(timeout=15)
        if self.status is None:
            raise RuntimeError("Connected, but no status arrived.")
        self.say(f"Connected. {reading(self.status)}")

    async def next_frame(self, timeout: float = 5.0) -> None:
        start = self.frames
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while self.frames == start and loop.time() < end:
            await asyncio.sleep(0.01)

    async def send(self, packet: bytes, frames: int = FRAMES_PER_SEND) -> None:
        """Queues `packet` on each of the next few status frames."""
        for _ in range(frames):
            log.info("send %s", packet.hex())
            self.treadmill.send(packet)
            await self.next_frame()

    # --- Packets ------------------------------------------------------------------

    def speed_packet(self, incline: int) -> bytes:
        """"Set speed" to the belt's current speed, so only byte 9 differs from what it is doing."""
        assert 0 <= incline <= MAX_START_INCLINE, "refusing an incline above the probe limit"
        return protocol.command(Command.START_OR_SET, self.hold_speed, self.status.imperial, 5, incline)

    def pause_packet(self) -> bytes:
        return protocol.command(Command.PAUSE, 0, self.status.imperial, 1, 0)

    # --- Steps ----------------------------------------------------------------------

    @property
    def belt_running(self) -> bool:
        return self.status.state == State.RUNNING and self.status.target_speed > 0

    async def observe(self, seconds: float) -> None:
        """Reads only."""
        self.say(f"\nWatching for {seconds:g}s. Optional: use the remote to raise the incline 2 or 3 steps, then "
                 "put it back to 0. Nothing is being sent.")
        last = None
        loop = asyncio.get_running_loop()
        end = loop.time() + seconds
        while loop.time() < end:
            await self.next_frame()
            now = (self.status.incline, self.status.target_incline, self.status.max_incline)
            if now != last:
                self.say(f"  {reading(self.status)}")
                last = now

    async def hold(self, seconds: float) -> bool:
        """Waits, watching frames. True if the user asked to stop."""
        loop = asyncio.get_running_loop()
        end = loop.time() + seconds
        while loop.time() < end:
            if self.key_pressed():
                return True
            await self.next_frame()
        return False

    async def run_test(self) -> None:
        """Byte 9 = 1, 2, 3, at the belt's own speed."""
        if not self.belt_running:
            self.say("\nThe belt isn't running. Start it slowly with the remote and run this again.")
            return
        if self.status.incline > MAX_START_INCLINE:
            self.say(f"\nThe incline is {self.status.incline}. Set it to {MAX_START_INCLINE} or lower with the "
                     "remote and run this again.")
            return
        self.hold_speed = self.status.target_speed
        self.restore_incline = self.status.incline
        self.say(f"\nSending the belt's own speed ({self.hold_speed / 1000:g} km/h) with incline byte 1, 2, 3. "
                 "Press any key to pause the belt at once.")
        for value in PROBE_VALUES:
            if not self.belt_running:  # A "set speed" would start a belt someone has stopped
                self.say("\nThe belt has stopped, so the test ends here.")
                return
            self.touched_incline = True
            await self.send(self.speed_packet(value))
            if await self.hold(self.settle):
                self.say("  Key pressed: pausing the belt.")
                self.aborted = True
                return
            self.results.append((value, self.status.incline, self.status.target_incline))
            self.say(f"  sent {value}: {reading(self.status)}")

    async def finish(self) -> None:
        """Puts the incline byte back, and pauses the belt if the test was aborted."""
        if self.treadmill is None or self.status is None:
            return
        try:
            if self.aborted:
                self.say("\nPausing the belt. If the incline is still raised, lower it with the remote.")
                await self.send(self.pause_packet())
            elif self.touched_incline:
                self.say(f"\nPutting the incline byte back to {self.restore_incline}. The belt keeps running.")
                if self.belt_running:
                    await self.send(self.speed_packet(self.restore_incline))
                else:  # Someone stopped it meanwhile
                    await self.send(self.pause_packet())
            self.touched_incline = False
            await self.treadmill.disconnect()
        except Exception as e:
            self.say(f"!! Cleanup problem: {e}. Pause the belt from the treadmill itself.")

    def verdict(self) -> str:
        if not self.results:
            return "No incline commands were sent, so there is no verdict yet."
        followed = [r for r in self.results if r[0] in (r[1], r[2])]
        if len(followed) == len(self.results):
            return "Byte 9 DOES set the incline: the reported incline/target followed every value sent."
        if followed:
            return "Byte 9 had a PARTIAL effect on some values. Send the log for a closer look."
        return "Byte 9 had NO visible effect on the reported incline or target."


async def run(probe: Probe, observe_seconds: float) -> None:
    try:
        await probe.connect()
        answer = await probe.ask(
            "\nNow start the belt SLOWLY with the remote, with the incline at 0.\n"
            "Press Enter when it's running (or type q to quit): "
        )
        if answer == "q":
            return
        await probe.next_frame()
        if not probe.belt_running:
            probe.say("\nThe belt isn't running yet. Start it with the remote and run this again.")
            return
        if observe_seconds > 0:
            await probe.observe(observe_seconds)
        while probe.status.incline > MAX_START_INCLINE:  # Wait for them to lower it rather than give up
            answer = await probe.ask(
                f"\nThe incline is {probe.status.incline}. Lower it to 0 with the remote, then press Enter here "
                "(or type q to quit): "
            )
            if answer == "q":
                return
            await probe.next_frame()
            if not probe.belt_running:
                probe.say("\nThe belt has stopped. Start it again and run this again.")
                return
        if (await probe.ask("\nType `go belt` to send the test (the belt keeps its speed), anything else to stop: ")) == "go belt":
            await probe.run_test()
    except BaseException:
        probe.aborted = probe.touched_incline  # Something went wrong mid-test: stop the belt
        raise
    finally:
        await probe.finish()
        probe.say("\n" + probe.verdict())


def windows_key_pressed() -> bool:
    try:
        import msvcrt
    except ImportError:
        return False  # Elsewhere, Ctrl+C is the way out
    if msvcrt.kbhit():
        msvcrt.getch()
        return True
    return False


async def ask_user(prompt: str) -> str:
    return (await asyncio.get_running_loop().run_in_executor(None, input, prompt)).strip().lower()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("-a", "--address", help="treadmill address (default: the last one the app used)")
    parser.add_argument("--observe", type=float, default=15.0, help="seconds of read-only watching first (0 to skip)")
    args = parser.parse_args()

    config = Config.load()
    address = args.address or config.address
    if not address:
        print("No treadmill saved yet. Pass --address, or connect once with the app.")
        return 2
    variant = config.variant if address.upper() == (config.address or "").upper() else None

    log_file = Path.cwd() / f"incline-probe-{datetime.now():%Y%m%d-%H%M%S}.log"
    logging.basicConfig(
        filename=log_file, level=logging.DEBUG, encoding="utf-8",
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    print(f"Logging to {log_file}")

    from pitpat import ble

    probe = Probe(ble.Treadmill, address, variant, ask_user, windows_key_pressed, say=lambda m: (print(m), log.info(m))[0])
    try:
        asyncio.run(run(probe, args.observe))
    except KeyboardInterrupt:
        print("\nInterrupted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
