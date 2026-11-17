"""Entry point for the desktop app: `pitpat-gui` or `python -m pitpat.gui`."""
import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler

from ..config import CONFIG_PATH, Config, app_dir
from ..protocol import VARIANTS

APP_ID = "Benevia.PitPat"


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="pitpat-gui",
        description="PitPat treadmill control: window and tray icon.",
        epilog=f"Settings (last treadmill, presets) are kept in {CONFIG_PATH}",
    )
    parser.add_argument("speed", nargs="?", type=float, help="start the belt at this speed once connected")
    parser.add_argument("--minimized", action="store_true", help="start in the tray without opening the window")
    parser.add_argument("-a", "--address", help="connect to this treadmill instead of the last one used")
    parser.add_argument("--scan", action="store_true", help="pick a treadmill from a scan instead of the last one used")
    parser.add_argument("--variant", choices=list(VARIANTS), help="force a hardware variant instead of detecting it")
    parser.add_argument("-v", "--verbose", action="store_true", help="log every status frame")
    args = parser.parse_args()

    log_file = app_dir() / "pitpat.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, handlers=[handler])

    config = Config.load()
    if config.variant not in VARIANTS:
        config.variant = None
    if args.scan:
        config.address = None
    if args.address:
        if args.address.upper() != (config.address or "").upper():
            config.variant = None
        config.address = args.address
    if args.variant:
        config.variant = args.variant

    if sys.platform == "win32":
        import ctypes

        # Gives the app its own taskbar identity and icon rather than python's.
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)

    from PySide6.QtWidgets import QApplication

    from .app import PitPatGui
    from .runner import AsyncRunner
    from .single import SingleInstance

    app = QApplication(sys.argv[:1])
    app.setApplicationName("PitPat")
    app.setQuitOnLastWindowClosed(False)  # It lives in the tray

    single = SingleInstance()
    if not single.claim():
        logging.getLogger(__name__).info("Already running; leaving it to the first instance")
        return 0

    runner = AsyncRunner()
    runner.start()
    gui = PitPatGui(app, config, runner, startup_speed=args.speed)
    single.activated.connect(gui.window.raise_to_front)
    app.aboutToQuit.connect(gui.close)
    gui.start(args.minimized)
    try:
        return app.exec()
    finally:
        runner.stop()


if __name__ == "__main__":
    sys.exit(main())
