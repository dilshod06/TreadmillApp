"""Test doubles shared by the TUI, controller and GUI tests."""
import asyncio

from pitpat.protocol import Status

from test_protocol import frame

RUNNING, PAUSED, STOPPED, IMPERIAL = 8, 16, 0, 128


class FakeTreadmill:
    instances = []

    def __init__(self, address, on_status, on_disconnect, variant=None):
        self.address = address
        self.on_status = on_status
        self.on_disconnect = on_disconnect
        self.variant = variant or "fba0"
        self.sent = []
        FakeTreadmill.instances.append(self)

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    def send(self, packet):
        self.sent.append(packet)
        future = asyncio.get_running_loop().create_future()
        future.set_result(None)
        return future

    def feed(self, **kwargs):
        self.on_status(Status.parse(frame(**kwargs)))
