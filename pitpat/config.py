"""Settings kept between runs: last treadmill, variant, speed presets and plans."""
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


log = logging.getLogger(__name__)


def app_dir() -> Path:
    """%APPDATA%\\pitpat on Windows, ~/.config/pitpat elsewhere."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "pitpat"


CONFIG_PATH = app_dir() / "config.json"


@dataclass
class Config:
    address: Optional[str] = None
    variant: Optional[str] = None  # None = detect on connect
    # Speeds for keys 1-9, in the treadmill's display unit (km/h or mph).
    presets: list[float] = field(default_factory=lambda: [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0])
    # Countdown plans as dicts; see plans.Plan.to_dict.
    plans: list[dict] = field(default_factory=list)
    # Whether the app may change the treadmill's incline (plans, incline buttons).
    # Off until the person turns it on; the app then only reminds.
    auto_incline: bool = False
    # Pause the belt when the treadmill says nobody is on it for this many seconds.
    off_belt_pause: bool = False
    off_belt_seconds: float = 10.0
    # The manual asks for a rest after a long run: pause the belt after this many
    # minutes of running, and don't start again until it has rested this long.
    rest_limit: bool = True
    rest_after_minutes: float = 120.0
    rest_minutes: float = 15.0
    # The units of the treadmill last seen, for showing the saved history in miles or kilometres.
    imperial: bool = True

    @classmethod
    def load(cls) -> "Config":
        try:
            data = json.loads(CONFIG_PATH.read_text())
            config = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
            config.presets = [float(x) for x in (list(config.presets) + cls().presets)[:9]]
            return config
        except FileNotFoundError:
            return cls()
        except Exception as e:
            log.warning("Ignoring unreadable %s: %s", CONFIG_PATH, e)
            return cls()

    def save(self) -> None:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(asdict(self), indent=2) + "\n")
