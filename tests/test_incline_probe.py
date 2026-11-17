"""tools/incline_probe.py against a simulated treadmill, plus the incline byte itself."""
import asyncio
import importlib.util
from pathlib import Path

import pytest

from pitpat import protocol
from pitpat.protocol import Command, State, Status

from fakes import PAUSED, RUNNING, STOPPED
from test_protocol import frame

SPEC = importlib.util.spec_from_file_location(
    "incline_probe", Path(__file__).resolve().parent.parent / "tools" / "incline_probe.py"
)
probe_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe_module)
Probe = probe_module.Probe

STATE_FLAGS = {State.RUNNING: RUNNING, State.PAUSED: PAUSED, State.STOPPED: STOPPED}
SPEED = 1500  # Wire units: the speed the person has the belt at


class SimTreadmill:
    """Streams status frames and obeys commands. With `responds`, it takes its
    incline from byte 9, but only while the belt runs, as the person says theirs does."""

    def __init__(self, address, on_status, on_disconnect, variant=None, *, responds=True, state=State.RUNNING, incline=0):
        self.address, self.on_status, self.on_disconnect = address, on_status, on_disconnect
        self.variant = variant or "fba0"
        self.responds = responds
        self.state = state
        self.incline = incline
        self.sent: list[bytes] = []
        self.task = None

    def _frame(self) -> bytes:
        running = self.state == State.RUNNING
        f = bytearray(frame(flags=STATE_FLAGS[self.state], target=SPEED if running else 0, speed=SPEED if running else 0))
        f[11], f[12], f[29] = self.incline, self.incline, 12
        f[-2] = protocol._xor(f[1:-2])
        return bytes(f)

    async def connect(self):
        async def stream():
            while True:
                self.on_status(Status.parse(self._frame()))
                await asyncio.sleep(0.003)

        self.task = asyncio.get_running_loop().create_task(stream())

    async def disconnect(self):
        if self.task:
            self.task.cancel()

    def send(self, packet):
        self.sent.append(packet)
        self.state = {Command.PAUSE: State.PAUSED, Command.START_OR_SET: State.RUNNING, Command.STOP: State.STOPPED}[
            Command(packet[12] & 7)
        ]
        if self.responds and self.state == State.RUNNING:
            self.incline = packet[9]
        future = asyncio.get_running_loop().create_future()
        future.set_result(None)
        return future


def make_probe(answers=("", "go belt"), key=lambda: False, **sim_kwargs):
    sim = {}

    def factory(*args):
        sim["t"] = SimTreadmill(*args, **sim_kwargs)
        return sim["t"]

    replies = iter(answers)
    prompts = []

    async def ask(prompt):
        prompts.append(prompt)
        return next(replies, "no")

    said = []
    probe = Probe(factory, "AA:BB", None, ask, key, say=said.append, settle=0.02)
    return probe, sim, said, prompts


async def go(probe):
    await probe_module.run(probe, observe_seconds=0.02)


def set_packet(incline):
    return protocol.command(Command.START_OR_SET, SPEED, False, 5, incline)


def pause_packet():
    return protocol.command(Command.PAUSE, 0, False, 1, 0)


async def test_a_treadmill_that_follows_byte_9_is_detected():
    probe, sim, said, _ = make_probe()
    await go(probe)
    t = sim["t"]
    assert probe.results == [(1, 1, 1), (2, 2, 2), (3, 3, 3)]
    assert "DOES set the incline" in said[-1]
    # Only "set speed" at the belt's own speed goes out, each the normal frame with just byte 9 changed.
    assert {p[12] & 7 for p in t.sent} == {Command.START_OR_SET}
    assert {int.from_bytes(p[6:8], "big") for p in t.sent} == {SPEED}
    assert [p for p in t.sent if p[9] == 2] == [set_packet(2)] * probe_module.FRAMES_PER_SEND
    assert [p[9] for p in dict.fromkeys(t.sent)] == [1, 2, 3, 0]  # Then the incline byte goes back
    assert t.state == State.RUNNING and t.incline == 0  # The person's belt keeps running


async def test_a_treadmill_that_ignores_byte_9_is_reported_so():
    probe, sim, said, _ = make_probe(responds=False)
    await go(probe)
    assert all(incline == 0 for _, incline, _ in probe.results)
    assert "NO visible effect" in said[-1]
    assert sim["t"].sent[-1] == set_packet(0)
    assert sim["t"].state == State.RUNNING


async def test_nothing_is_sent_while_the_belt_is_stopped():
    probe, sim, said, prompts = make_probe(state=State.STOPPED)
    await go(probe)
    assert sim["t"].sent == [] and len(prompts) == 1
    assert any("isn't running" in m for m in said)


async def test_quitting_at_the_first_prompt_sends_nothing():
    probe, sim, _, _ = make_probe(answers=("q",))
    await go(probe)
    assert sim["t"].sent == []


async def test_the_test_needs_the_exact_phrase():
    probe, sim, said, _ = make_probe(answers=("", "go"))
    await go(probe)
    assert sim["t"].sent == [] and "no verdict yet" in said[-1]


async def test_it_sends_nothing_while_the_incline_is_raised():
    probe, sim, said, prompts = make_probe(answers=("", "q"), incline=12)
    await go(probe)
    assert sim["t"].sent == []
    assert "The incline is 12. Lower it to 0" in prompts[-1]


async def test_it_waits_for_the_incline_to_be_lowered_then_carries_on():
    probe, sim, said, prompts = make_probe(incline=12)
    replies = iter(["", "", "go belt"])  # Start belt, "it's lowered", the go-ahead

    async def ask(prompt):
        prompts.append(prompt)
        if "Lower it to 0" in prompt:
            sim["t"].incline = 0  # The person uses the remote
        return next(replies)

    probe.ask = ask
    await go(probe)
    assert probe.results == [(1, 1, 1), (2, 2, 2), (3, 3, 3)]
    assert sim["t"].incline == 0 and sim["t"].state == State.RUNNING


async def test_it_gives_up_if_the_belt_stops_while_waiting():
    probe, sim, said, _ = make_probe(answers=("", ""), incline=12)
    original = probe.ask

    async def ask(prompt):
        if "Lower it to 0" in prompt:
            sim["t"].state = State.PAUSED
        return await original(prompt)

    probe.ask = ask
    await go(probe)
    assert sim["t"].sent == [] and any("belt has stopped" in m for m in said)


async def test_a_keypress_pauses_the_belt_at_once():
    probe, sim, said, _ = make_probe(key=lambda: True, responds=False)
    await go(probe)
    t = sim["t"]
    assert {p[9] for p in t.sent if p[12] & 7 == Command.START_OR_SET} == {1}  # Never got to 2
    assert t.sent[-1] == pause_packet() and t.state == State.PAUSED
    assert any("lower it with the remote" in m for m in said)


async def test_an_error_midway_pauses_the_belt():
    probe, sim, _, _ = make_probe(responds=False)

    async def boom(seconds):
        raise RuntimeError("boom")

    probe.hold = boom
    with pytest.raises(RuntimeError):
        await go(probe)
    assert sim["t"].sent[-1] == pause_packet() and sim["t"].state == State.PAUSED


async def test_an_error_before_anything_was_sent_leaves_the_belt_alone():
    probe, sim, _, _ = make_probe()

    async def boom(seconds):
        raise RuntimeError("boom")

    probe.observe = boom
    with pytest.raises(RuntimeError):
        await go(probe)
    assert sim["t"].sent == [] and sim["t"].state == State.RUNNING


async def test_someone_stopping_the_belt_midway_is_not_restarted():
    probe, sim, _, _ = make_probe(responds=False)
    original = probe.hold

    async def stop_then_hold(seconds):
        sim["t"].state = State.PAUSED  # Stopped from the remote
        return await original(seconds)

    probe.hold = stop_then_hold
    await go(probe)
    t = sim["t"]
    # Only value 1 went out. Nothing "set speed" followed, since that would start the belt again.
    assert {p[9] for p in t.sent if p[12] & 7 == Command.START_OR_SET} == {1}
    assert t.sent[-1] == pause_packet() and t.state == State.PAUSED


async def test_the_probe_refuses_values_above_its_limit():
    probe, *_ = make_probe()
    await probe.connect()
    probe.hold_speed = SPEED
    with pytest.raises(AssertionError):
        probe.speed_packet(4)
    await probe.finish()


# --- The incline byte in the command builder ----------------------------------------------

def test_incline_defaults_to_zero_and_only_changes_byte_9():
    plain = protocol.command(Command.START_OR_SET, 1500, True)
    tilted = protocol.command(Command.START_OR_SET, 1500, True, 1, 5)
    assert plain[9] == 0 and tilted[9] == 5
    assert plain == protocol.start(1500, True)  # The builders the app uses are untouched
    assert [i for i in range(23) if plain[i] != tilted[i]] == [9, 21]  # Byte 9 and the checksum
    assert tilted[21] == protocol._xor(tilted[1:21])


def test_incline_must_fit_in_a_byte():
    for bad in (-1, 256):
        with pytest.raises(ValueError):
            protocol.command(Command.PAUSE, incline=bad)
