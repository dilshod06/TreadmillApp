"""The terminal UI."""
import logging
import os
from typing import Optional

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import DataTable, Digits, Footer, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from . import ble
from .config import Config
from .controller import (
    CONNECTED, CONNECTING, FAILED, RECONNECTING, STATE_LABELS, Controller, TreadmillFactory, detail_rows, fmt_duration,
)

log = logging.getLogger(__name__)


class DevicePicker(ModalScreen[Optional[str]]):
    """Scans for treadmills and returns the chosen address (None if cancelled)."""

    BINDINGS = [
        Binding("ctrl+r", "scan", "Rescan"),
        Binding("r", "scan", show=False),  # When the address box isn't focused
        Binding("escape", "dismiss(None)", "Cancel"),
    ]

    def __init__(self, saved: Optional[str], auto_pick: bool):
        super().__init__()
        self.saved = saved
        self.auto_pick = auto_pick

    def compose(self) -> ComposeResult:
        with Vertical(id="picker"):
            yield Label("Choose your treadmill", id="picker-title")
            yield Static(id="scan-status")
            yield OptionList(id="devices")
            yield Input(placeholder="…or type an address and press Enter", id="manual")
            yield Footer(compact=True)

    def on_mount(self) -> None:
        self.action_scan()

    @work(exclusive=True)
    async def action_scan(self) -> None:
        devices_list = self.query_one("#devices", OptionList)
        status = self.query_one("#scan-status", Static)
        devices_list.clear_options()
        devices_list.display = False
        status.update("Scanning… make sure the treadmill is on.")
        try:
            devices = await ble.discover()
        except Exception as e:
            log.exception("Scan failed")
            status.update(f"[red]Scan failed:[/] {e}\nIs Bluetooth on? Press [b]Ctrl+R[/] to retry.")
            self.query_one("#manual").focus()
            return

        if not devices:
            status.update("No treadmills found. Check it's on and not connected to another app, then press [b]Ctrl+R[/].")
            self.query_one("#manual").focus()
            return
        if self.auto_pick and len(devices) == 1:
            self.dismiss(devices[0].address)
            return

        for d in devices:
            signal = f"{d.rssi} dBm" if d.rssi is not None else "known, not advertising"
            saved = "  ★ last used" if self.saved and d.address.upper() == self.saved.upper() else ""
            devices_list.add_option(Option(f"{d.name}  [dim]{d.address}  {signal}[/]{saved}", id=d.address))
        status.update(f"Found {len(devices)}. Pick one with ↑↓ and Enter.")
        devices_list.display = True
        devices_list.highlighted = 0
        devices_list.focus()

    @on(OptionList.OptionSelected, "#devices")
    def picked(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    @on(Input.Submitted, "#manual")
    def typed(self, event: Input.Submitted) -> None:
        if event.value.strip():
            self.dismiss(event.value.strip())


class SpeedPrompt(ModalScreen[Optional[float]]):
    """Asks for a speed, optionally pre-filled with the digit that opened it."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    def __init__(self, title: str, initial: str = ""):
        super().__init__()
        self.title_text = title
        self.initial = initial

    def compose(self) -> ComposeResult:
        with Vertical(id="prompt"):
            yield Label(self.title_text)
            yield Input(self.initial, placeholder="e.g. 2.5", type="number", id="speed-input")

    @on(Input.Submitted)
    def submitted(self, event: Input.Submitted) -> None:
        try:
            self.dismiss(float(event.value))
        except ValueError:
            self.notify("Enter a number, like 2.5", severity="warning")


class Dashboard(Screen):
    BINDINGS = [
        Binding("space", "toggle", "Start/Pause"),
        Binding("up,plus,equals_sign", "nudge(0.1)", "Speed", key_display="↑↓"),
        Binding("down,minus", "nudge(-0.1)", show=False),
        Binding("1", "preset(1)", "Preset", key_display="1-9"),
        *(Binding(str(n), f"preset({n})", show=False) for n in range(2, 10)),
        Binding("enter", "type_speed", "Exact", key_display="⏎"),
        Binding("s", "stop", "Stop"),
        Binding("p", "save_preset", "Save"),
        Binding("d", "toggle_details", "Details"),
        Binding("c", "pick_device", "Devices"),
        Binding("escape", "cancel", show=False),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, controller: Controller):
        super().__init__()
        self.controller = controller
        self.saving_preset = False

    def compose(self) -> ComposeResult:
        yield Static("[dim]●[/] Not connected", id="connection")
        with Vertical(id="main"):
            yield Label(id="state")
            yield Digits("--", id="speed")
            yield Label(id="target")
            with Horizontal(id="stats"):
                for key, title in (("time", "Time"), ("distance", "Distance"), ("steps", "Steps"), ("calories", "Calories")):
                    with Vertical(classes="stat"):
                        yield Label(title, classes="stat-title")
                        yield Label("--", id=key, classes="stat-value")
            yield Static(id="presets")
            details = DataTable(id="details", show_header=False, cursor_type="none")
            details.can_focus = False  # Would otherwise swallow Enter and the arrow keys.
            yield details
        yield Footer(compact=True)

    def on_mount(self) -> None:
        self.query_one("#details", DataTable).add_columns("Field", "Value")
        c = self.controller
        c.on_change = self.refresh_view
        c.on_notify = lambda message, severity, title: self.notify(message, title=title, severity=severity)
        c.on_pick_device = lambda auto_pick: self.action_pick_device(auto_pick=auto_pick)
        self.refresh_view()
        c.start()

    # --- Actions --------------------------------------------------------------

    def action_toggle(self) -> None:
        self.controller.toggle()

    def action_stop(self) -> None:
        self.controller.stop()

    def action_nudge(self, step: float) -> None:
        self.controller.nudge(step)

    def action_preset(self, n: int) -> None:
        if self.saving_preset:
            self.saving_preset = False
            self.controller.save_preset(n)
            self.refresh_view()
        else:
            self.controller.go_preset(n)

    def action_type_speed(self) -> None:
        s = self.controller.status
        if s is None:
            self.controller.notify("Not connected to a treadmill yet.", "warning")
            return

        def chosen(speed: Optional[float]) -> None:
            if speed is not None:
                self.controller.set_speed(speed)

        self.app.push_screen(SpeedPrompt(f"Speed in {s.speed_unit}, then Enter (Esc cancels)"), chosen)

    def action_save_preset(self) -> None:
        speed = self.controller.current_speed()
        if speed is None:
            self.notify("Get the belt running at the speed you want first.")
            return
        self.saving_preset = True
        self.notify(f"Press 1–9 to save {speed:.1f} there (Esc cancels).")

    def action_cancel(self) -> None:
        self.saving_preset = False

    def action_toggle_details(self) -> None:
        self.query_one("#details").toggle_class("shown")

    def action_pick_device(self, auto_pick: bool = False) -> None:
        self.controller.cancel_connect()

        def chosen(address: Optional[str]) -> None:
            if address:
                self.controller.connect(address)

        self.app.push_screen(DevicePicker(self.controller.config.address, auto_pick), chosen)

    async def action_quit(self) -> None:
        """Pauses the belt if it's moving, then exits."""
        await self.controller.shutdown()
        self.app.exit()

    # --- Rendering ----------------------------------------------------------

    def refresh_view(self) -> None:
        c = self.controller
        s = c.status
        state = self.query_one("#state", Label)
        target = self.query_one("#target", Label)
        state.set_classes(STATE_LABELS[s.state].lower() if s else "")
        self.render_connection()

        if s is None:
            idle = c.treadmill is None and not c.connecting
            state.update("NOT CONNECTED" if idle else "WAITING FOR TREADMILL")
            self.query_one("#speed", Digits).update("--")
            target.update("Press [b]c[/] to choose a treadmill" if idle else "")
            for key in ("time", "distance", "steps", "calories"):
                self.query_one(f"#{key}", Label).update("--")
        else:
            state.update(STATE_LABELS[s.state])
            self.query_one("#speed", Digits).update(f"{s.to_display(s.speed):.1f}")
            line = f"{s.speed_unit}  ·  target {s.to_display(s.target_speed):.1f}  ·  max {s.to_display(s.max_speed):.1f}"
            if isinstance(c.goal, float):
                line += f"  ·  [b]→ {c.goal:.1f}[/]"
            elif c.goal == "stop":
                line += "  ·  [b]stopping…[/]"
            target.update(line)
            self.query_one("#time", Label).update(fmt_duration(s.duration_s))
            self.query_one("#distance", Label).update(f"{s.distance:.2f} {s.distance_unit}")
            self.query_one("#steps", Label).update("--" if s.steps is None else f"{s.steps:,}")
            self.query_one("#calories", Label).update(f"{s.calories} kcal")

        current = c.current_speed()
        goal = c.goal if isinstance(c.goal, float) else None
        max_speed = s.to_display(s.max_speed) if s and s.max_speed else float("inf")
        chips = []
        for n, speed in enumerate(c.config.presets, 1):
            active = speed == (goal if goal is not None else current)
            chip = f"[b $accent]{n}[/] {speed:.1f}"
            if active:
                chip = f"[reverse] {chip} [/]"
            elif speed > max_speed + 0.05:
                chip = f"[dim] {chip} [/]"  # Faster than this treadmill goes
            else:
                chip = f" {chip} "
            chips.append(chip)
        hint = "  [b]Press 1–9 to save here[/]" if self.saving_preset else ""
        self.query_one("#presets", Static).update("Presets " + " ".join(chips) + hint)

        self.update_details()

    def render_connection(self) -> None:
        c = self.controller
        text = {
            CONNECTING: f"[yellow]●[/] Connecting to {c.conn_address}…  [dim](c to choose another)[/]",
            RECONNECTING: f"[yellow]●[/] Reconnecting to {c.conn_address}…  [dim](c to choose another)[/]",
            CONNECTED: f"[green]●[/] Connected to {c.conn_address}",
            FAILED: f"[red]●[/] Couldn't connect to {c.conn_address}",
        }.get(c.conn_state, "[dim]●[/] Not connected")
        self.query_one("#connection", Static).update(text)

    def update_details(self) -> None:
        table = self.query_one("#details", DataTable)
        table.clear()
        table.add_rows(detail_rows(self.controller.view()))


def terminal_is_light() -> bool:
    """Best guess from COLORFGBG ("fg;bg"), which some terminals set; else dark."""
    background = os.environ.get("COLORFGBG", "").split(";")[-1]
    return background in ("7", "15")


class PitPatApp(App):
    TITLE = "PitPat"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [Binding("ctrl+c", "quit", show=False, priority=True)]
    CSS = """
    #connection { padding: 0 1; border-bottom: solid ansi_bright_black; height: 2; }
    #main { align: center top; padding: 1 2; }
    #state { width: 100%; text-align: center; text-style: bold; color: $text-muted; }
    #state.running { color: $success; }
    #state.starting { color: $warning; }
    #state.paused { color: $warning; }
    #speed { width: 100%; text-align: center; margin: 1 0 0 0; }
    #target { width: 100%; text-align: center; color: $text-muted; margin-bottom: 1; }
    #stats { height: auto; width: 100%; align-horizontal: center; }
    .stat { width: 18; height: auto; border: round ansi_bright_black; padding: 0 1; }
    .stat-title { color: $text-muted; }
    .stat-value { text-style: bold; }
    #presets { width: 100%; text-align: center; margin: 1 0; }
    #details { display: none; height: auto; max-height: 16; margin: 0 4; }
    #details.shown { display: block; }
    DevicePicker, SpeedPrompt { align: center middle; }
    #picker { width: 80; max-width: 100%; height: auto; max-height: 90%; border: round $primary; background: $background; padding: 1 2; }
    #picker-title { text-style: bold; margin-bottom: 1; }
    #devices { height: auto; max-height: 12; margin: 1 0; }
    #devices > .option-list--option-highlighted { background: ansi_blue; color: ansi_bright_white; text-style: bold; }
    #prompt { width: 44; height: auto; border: round $primary; background: $background; padding: 1 2; }
    """

    def __init__(self, config: Config, startup_speed: Optional[float] = None, make_treadmill: Optional[TreadmillFactory] = None):
        super().__init__()
        self.controller = Controller(config, make_treadmill or ble.Treadmill, startup_speed)
        self.dashboard = Dashboard(self.controller)

    def on_mount(self) -> None:
        # Draw with the terminal's own background and palette rather than Textual's.
        self.theme = "ansi-light" if terminal_is_light() else "ansi-dark"
        self.push_screen(self.dashboard)

    async def action_quit(self) -> None:
        await self.dashboard.action_quit()
