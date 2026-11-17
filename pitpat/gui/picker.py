"""Dialog that scans for treadmills and returns the chosen address."""
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout,
)

from .backend import Backend


class PickerDialog(QDialog):
    def __init__(self, backend: Backend, saved: Optional[str], auto_pick: bool, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.saved = saved
        self.auto_pick = auto_pick
        self.address: Optional[str] = None
        self.setWindowTitle("Choose your treadmill")
        self.setMinimumWidth(460)

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.devices = QListWidget()
        self.devices.itemActivated.connect(lambda item: self.choose(item.data(Qt.ItemDataRole.UserRole)))
        self.devices.itemSelectionChanged.connect(self._sync_buttons)
        self.manual = QLineEdit()
        self.manual.setPlaceholderText("…or type an address and press Enter")
        self.manual.textChanged.connect(self._sync_buttons)
        self.manual.returnPressed.connect(self._connect_clicked)

        self.rescan = QPushButton("Rescan")
        self.rescan.clicked.connect(self.scan)
        self.connect_button = QPushButton("Connect")
        self.connect_button.setDefault(True)
        self.connect_button.clicked.connect(self._connect_clicked)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.addWidget(self.rescan)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(self.connect_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.status)
        layout.addWidget(self.devices)
        layout.addWidget(self.manual)
        layout.addLayout(buttons)

        backend.scan_finished.connect(self._scanned)
        self.finished.connect(lambda _: backend.scan_finished.disconnect(self._scanned))
        self.scan()

    def scan(self) -> None:
        self.devices.clear()
        self.status.setText("Scanning… make sure the treadmill is on.")
        self.rescan.setEnabled(False)
        self._sync_buttons()
        self.backend.scan()

    def _scanned(self, devices, error: str) -> None:
        self.rescan.setEnabled(True)
        if error:
            self.status.setText(f"Scan failed: {error}\nIs Bluetooth on? Press Rescan to retry.")
            self.manual.setFocus()
        elif not devices:
            self.status.setText("No treadmills found. Check it's on and not connected to another app, then press Rescan.")
            self.manual.setFocus()
        elif self.auto_pick and len(devices) == 1:
            self.choose(devices[0].address)
            return
        else:
            for d in devices:
                signal = f"{d.rssi} dBm" if d.rssi is not None else "known, not advertising"
                last = "   ★ last used" if self.saved and d.address.upper() == self.saved.upper() else ""
                item = QListWidgetItem(f"{d.name}   {d.address}   {signal}{last}")
                item.setData(Qt.ItemDataRole.UserRole, d.address)
                self.devices.addItem(item)
            self.devices.setCurrentRow(0)
            self.devices.setFocus()
            self.status.setText(f"Found {len(devices)}. Pick one and press Connect.")
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        self.connect_button.setEnabled(bool(self.manual.text().strip()) or self.devices.currentItem() is not None)

    def _connect_clicked(self) -> None:
        typed = self.manual.text().strip()
        item = self.devices.currentItem()
        if typed:
            self.choose(typed)
        elif item is not None:
            self.choose(item.data(Qt.ItemDataRole.UserRole))

    def choose(self, address: str) -> None:
        self.address = address
        self.accept()
