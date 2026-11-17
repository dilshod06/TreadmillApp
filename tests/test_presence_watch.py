"""tools/presence_watch.py: finding the byte that follows the person, against a simulated treadmill."""
import asyncio
import importlib.util
from pathlib import Path

from fakes import RUNNING
from pitpat import protocol
from pitpat.protocol import Status
from test_protocol import frame

SPEC = importlib.util.spec_from_file_location(
    "presence_watch", Path(__file__).resolve().parent.parent / "tools" / "presence_watch.py"
)
pw = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pw)


def raw(on_belt: bool, tick: int, running: bool = True) -> bytes:
    f = bytearray(frame(flags=RUNNING if running else 0, target=1000 if running else 0, speed=1000 if running else 0,
                        duration_ms=tick * 1000))
    f[32] = 1 if on_belt else 0  # The "sensor"
    f[24] = 80 + tick % 5  # Something noisy that doesn't follow the person
    f[-2] = protocol._xor(f[1:-2])
    return bytes(f)


def samples(sensor_offset=32, noise=True):
    out = []
    for number, label in enumerate(pw.PHASES):
        for tick in range(15):
            f = bytearray(raw(label == "ON", number * 15 + tick))
            f[sensor_offset] = 1 if label == "ON" else 0
            if not noise:
                f[24] = 80
            out.append((number, label, float(tick), bytes(f)))
    return out


def test_it_finds_the_byte_that_follows_the_person_and_not_the_noise():
    found = pw.analyze(samples(), grace=3)
    offsets = [hit["offset"] for hit in found]
    assert 32 in offsets
    assert 24 not in offsets  # Values 80-84 appear both on and off
    assert 20 not in offsets and 21 not in offsets  # The duration counter keeps changing
    hit = next(h for h in found if h["offset"] == 32)
    assert hit["on"] == [1] and hit["off"] == [0]
    assert "byte 32: ON 1 | OFF 0" in "\n".join(pw.report(found))


def test_the_first_seconds_of_each_stretch_are_ignored_while_people_move():
    data = samples()
    # The sensor lags by 2 seconds at each change: wrong values early in every stretch
    lagged = [(n, l, t, bytes(f[:32] + bytes([0 if l == "ON" else 1]) + f[33:]) if t < 2 else f) for n, l, t, f in data]
    assert 32 not in [h["offset"] for h in pw.analyze(lagged, grace=0)]
    assert 32 in [h["offset"] for h in pw.analyze(lagged, grace=3)]


def test_no_pattern_is_reported_honestly():
    flat = [(n, l, float(t), raw(True, 0)) for n, l in enumerate(pw.PHASES) for t in range(15)]
    found = pw.analyze(flat, grace=3)
    assert found == []
    assert "No byte separated ON from OFF" in pw.report(found)[0]


def test_too_few_stretches_prove_nothing():
    one_each = [s for s in samples() if s[0] < 2]
    assert pw.analyze(one_each, grace=3) == []


class SimTreadmill:
    def __init__(self, address, on_status, on_disconnect, variant=None):
        self.on_status, self.on_raw, self.task = on_status, None, None
        self.variant = "fba0"
        self.on_belt = True
        self.running = True

    async def connect(self):
        async def stream():
            tick = 0
            while True:
                data = raw(self.on_belt, tick, self.running)
                self.on_raw(data)
                status = Status.parse(data)
                assert status is not None  # A well-formed frame
                self.on_status(status)
                tick += 1
                await asyncio.sleep(0.002)

        self.task = asyncio.get_running_loop().create_task(stream())

    async def disconnect(self):
        self.task.cancel()


async def test_a_recording_run_labels_frames_by_stretch_and_never_sends():
    made = []

    def factory(*args):
        made.append(SimTreadmill(*args))
        return made[0]

    said = []

    async def ask(prompt):
        return ""

    rec = pw.Recorder(factory, "AA:BB", None, ask, say=said.append, seconds=0.02, grace=0.005)

    async def mover():  # Steps off and on with the instructions
        seen = 0
        while True:
            await asyncio.sleep(0.001)
            if rec.current[1]:
                made[0].on_belt = rec.current[1] == "ON"

    task = asyncio.create_task(mover())
    await pw.run(rec)
    task.cancel()
    assert not hasattr(made[0], "send")  # Read-only: the simulated treadmill has no way to be commanded
    assert {n for n, *_ in rec.samples} == set(range(6))
    assert any("byte 32" in line for line in said)
    assert sum("Stand ON" in line for line in said) == 3 and sum("Step OFF" in line for line in said) == 3


async def test_a_run_that_never_connected_reports_nothing_about_bytes():
    said = []

    class Unreachable(SimTreadmill):
        async def connect(self):
            raise RuntimeError("Device not found")

        async def disconnect(self):
            pass

    async def ask(prompt):
        return ""

    rec = pw.Recorder(Unreachable, "AA:BB", None, ask, say=said.append, seconds=0.01)
    try:
        await pw.run(rec)
    except RuntimeError:
        pass
    assert not any("byte" in line for line in said)


async def test_it_waits_for_the_belt_to_run_before_recording():
    prompts, said, made = [], [], []

    def factory(*args):
        made.append(SimTreadmill(*args))
        made[0].running = False  # The belt is stopped when it connects
        return made[0]

    async def ask(prompt):
        prompts.append(prompt)
        if len(prompts) == 2:
            made[0].running = True  # They start the belt with the remote
        return ""

    rec = pw.Recorder(factory, "AA:BB", None, ask, say=said.append, seconds=0.01, grace=0.002)
    await pw.run(rec)
    assert len(prompts) == 2 and "isn't running" in prompts[1]  # Asked again, and recorded only after it ran
    assert {n for n, *_ in rec.samples} == set(range(6))
    assert all(Status.parse(frame).state.name == "RUNNING" for *_, frame in rec.samples)  # Nothing from the stopped belt
