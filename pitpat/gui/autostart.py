"""Starting the tray app at login: the Run registry key on Windows, an XDG
autostart entry elsewhere."""
import os
import subprocess
import sys
from pathlib import Path

APP_NAME = "PitPat"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"  # Replaced in tests.


def command() -> list[str]:
    """Launches the GUI without a console window, straight into the tray."""
    python = Path(sys.executable)
    if sys.platform == "win32" and python.with_name("pythonw.exe").exists():
        python = python.with_name("pythonw.exe")
    return [str(python), "-m", "pitpat.gui", "--minimized"]


def _desktop_entry() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "autostart" / "pitpat.desktop"


def is_enabled() -> bool:
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                winreg.QueryValueEx(key, APP_NAME)
            return True
        except FileNotFoundError:
            return False
    return _desktop_entry().exists()


def set_enabled(enabled: bool) -> None:
    if sys.platform == "win32":
        import winreg

        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, subprocess.list2cmdline(command()))
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
        return
    entry = _desktop_entry()
    if enabled:
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text(
            "[Desktop Entry]\nType=Application\nName=PitPat\n"
            f"Exec={subprocess.list2cmdline(command())}\nX-GNOME-Autostart-enabled=true\n"
        )
    elif entry.exists():
        entry.unlink()
