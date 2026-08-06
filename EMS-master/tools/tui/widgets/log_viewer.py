"""RichLog widget for per-process log output."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import RichLog, Static

from tools.tui.models import ProcessState


class LogViewer(Static):
    """Scrollable log output for the selected process."""

    DEFAULT_CSS = """
    LogViewer {
        height: 16;
        border-top: solid $accent;
    }
    LogViewer #log-header {
        height: 1;
        background: $accent;
        color: $text;
        padding: 0 1;
    }
    LogViewer RichLog {
        height: 1fr;
    }
    """

    MIN_HEIGHT = 6
    MAX_HEIGHT = 40

    def __init__(self) -> None:
        super().__init__()
        self._current_process_id: str | None = None
        self._height: int = 16

    def grow(self) -> None:
        """Increase log panel height."""
        self._height = min(self._height + 4, self.MAX_HEIGHT)
        self.styles.height = self._height

    def shrink(self) -> None:
        """Decrease log panel height."""
        self._height = max(self._height - 4, self.MIN_HEIGHT)
        self.styles.height = self._height

    def compose(self) -> ComposeResult:
        yield Static("LOG: (none selected)", id="log-header")
        yield RichLog(highlight=True, markup=True, wrap=True, max_lines=2000)

    def show_process(self, process_id: str, state: ProcessState) -> None:
        """Switch to showing logs for a different process."""
        self._current_process_id = process_id
        header = self.query_one("#log-header", Static)
        exit_info = ""
        if state.exit_code is not None:
            exit_info = f"  (exit code: {state.exit_code})"
        header.update(f"LOG: {state.config.name}{exit_info}")

        log_widget = self.query_one(RichLog)
        log_widget.clear()
        for line in state.log_lines:
            log_widget.write(line)

    def append_line(self, process_id: str, line: str) -> None:
        """Append a log line if it's for the currently displayed process."""
        if process_id == self._current_process_id:
            log_widget = self.query_one(RichLog)
            log_widget.write(line)

    @property
    def current_process_id(self) -> str | None:
        return self._current_process_id
