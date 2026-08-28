"""Modal dialog showing crash details for a process."""

from __future__ import annotations

import time

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, RichLog, Static

from tools.tui.models import ProcessState, ProcessStatus


class ErrorModal(ModalScreen[bool]):
    """Modal showing crash details. Returns True if user wants to restart."""

    DEFAULT_CSS = """
    ErrorModal {
        align: center middle;
    }
    ErrorModal #error-dialog {
        width: 80;
        max-width: 90%;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: solid $error;
        padding: 1 2;
    }
    ErrorModal #error-title {
        text-style: bold;
        color: $error;
        width: 100%;
        content-align: center middle;
        margin-bottom: 1;
    }
    ErrorModal #error-info {
        margin-bottom: 1;
    }
    ErrorModal #error-stderr {
        height: auto;
        max-height: 20;
        border: solid $error 40%;
        margin-bottom: 1;
    }
    ErrorModal .button-row {
        height: 3;
        align: center middle;
        layout: horizontal;
    }
    ErrorModal Button {
        margin: 0 1;
    }
    """

    BINDINGS = [
        ("escape", "dismiss_modal", "Close"),
        ("r", "restart", "Restart"),
    ]

    def __init__(self, process_id: str, state: ProcessState) -> None:
        super().__init__()
        self._process_id: str = process_id
        self._state: ProcessState = state

    def compose(self) -> ComposeResult:
        state = self._state

        # Build info text
        crash_time_str = ""
        if state.crash_time:
            crash_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(state.crash_time))

        exit_info = f"Exit code: {state.exit_code}" if state.exit_code is not None else "Unknown"
        # Check if killed by signal
        if state.exit_code is not None and state.exit_code < 0:
            import signal

            try:
                sig_name = signal.Signals(-state.exit_code).name
                exit_info = f"Killed by signal: {sig_name} ({-state.exit_code})"
            except (ValueError, AttributeError):
                exit_info = f"Killed by signal: {-state.exit_code}"

        info_lines = [
            f"Process:  {state.config.name}",
            f"Status:   {exit_info}",
            f"Crashed:  {crash_time_str}",
            f"Restarts: {state.restart_count}",
            f"Command:  {state.config.command}",
        ]

        with Vertical(id="error-dialog"):
            yield Static("Process Crash Details", id="error-title")
            yield Static("\n".join(info_lines), id="error-info")

            # Stderr output
            stderr_log = RichLog(
                highlight=True,
                markup=False,
                wrap=True,
                max_lines=100,
                id="error-stderr",
            )
            yield stderr_log
            yield Static("[r] Restart  [Escape] Close", classes="button-row")

    def on_mount(self) -> None:
        stderr_log = self.query_one("#error-stderr", RichLog)
        if self._state.stderr_lines:
            for line in self._state.stderr_lines:
                stderr_log.write(line)
        else:
            # Fall back to last log lines that contain error-like content
            for line in list(self._state.log_lines)[-30:]:
                if any(kw in line.lower() for kw in ("error", "fatal", "traceback", "exception")):
                    stderr_log.write(line)
            if not stderr_log.lines:
                stderr_log.write("(no stderr captured)")

    def action_dismiss_modal(self) -> None:
        self.dismiss(False)

    def action_restart(self) -> None:
        self.dismiss(True)
