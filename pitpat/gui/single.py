"""Keeps to one running instance: a second launch pokes the first and exits.

Two instances would fight over the treadmill's single Bluetooth connection.
"""
import getpass

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


class SingleInstance(QObject):
    activated = Signal()  # Another launch asked us to show the window

    def __init__(self, key: str = "pitpat-gui"):
        super().__init__()
        self.name = f"{key}-{getpass.getuser()}"
        self.server: QLocalServer | None = None

    def claim(self) -> bool:
        """True if we're the first instance. Otherwise pokes the running one and returns False."""
        probe = QLocalSocket()
        probe.connectToServer(self.name)
        if probe.waitForConnected(500):
            probe.disconnectFromServer()
            return False
        QLocalServer.removeServer(self.name)  # Clears a stale socket left by a crash
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._poked)
        return self.server.listen(self.name)

    def _poked(self) -> None:
        while self.server and self.server.hasPendingConnections():
            self.server.nextPendingConnection().close()
        self.activated.emit()
