"""DataTable widget showing all EMS processes with color-coded status."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.message import Message
from textual.widgets import DataTable, Static

from tools.tui.models import ProcessState, ProcessStatus


STATUS_STYLES: dict[ProcessStatus, tuple[str, str]] = {
    # (display_text, rich_style)
    ProcessStatus.STOPPED: ("stopped", "dim"),
    ProcessStatus.STARTING: ("starting", "yellow"),
    ProcessStatus.RUNNING: ("running", "green"),
    ProcessStatus.CRASHED: ("ERROR", "bold red blink"),
    ProcessStatus.STOPPING: ("stopping", "cyan"),
    ProcessStatus.DONE: ("done", "green dim"),
}

COL_PHASE = "col_phase"
COL_PROCESS = "col_process"
COL_STATUS = "col_status"
COL_PID = "col_pid"
COL_UPTIME = "col_uptime"
COL_RST = "col_rst"


class ProcessSelected(Message):
    """Posted when the user moves cursor to a different process row."""

    def __init__(self, process_id: str) -> None:
        super().__init__()
        self.process_id: str = process_id


class ProcessTable(Static):
    """Process list table with status indicators."""

    DEFAULT_CSS = """
    ProcessTable {
        height: 1fr;
    }
    ProcessTable DataTable {
        height: 1fr;
    }
    """

    def __init__(self, process_ids: list[str], states: dict[str, ProcessState]) -> None:
        super().__init__()
        self._process_ids: list[str] = process_ids
        self._states: dict[str, ProcessState] = states
        self._get_phase: callable = lambda pid: "?"

    def set_phase_resolver(self, fn: callable) -> None:
        self._get_phase = fn

    def compose(self) -> ComposeResult:
        yield DataTable(cursor_type="row")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_column("Phase", key=COL_PHASE, width=12)
        table.add_column("Process", key=COL_PROCESS, width=20)
        table.add_column("Status", key=COL_STATUS, width=10)
        table.add_column("PID", key=COL_PID, width=8)
        table.add_column("Uptime", key=COL_UPTIME, width=10)
        table.add_column("#Rst", key=COL_RST, width=5)

        for pid in self._process_ids:
            state = self._states[pid]
            phase = self._get_phase(pid)
            table.add_row(
                phase,
                state.config.name,
                Text("stopped", style="dim"),
                "--",
                "--",
                "0",
                key=pid,
            )

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        row_key = str(event.row_key.value)
        if row_key in [p for p in self._process_ids]:
            self.post_message(ProcessSelected(row_key))

    def update_process(self, process_id: str) -> None:
        """Refresh a single row from its ProcessState."""
        state = self._states.get(process_id)
        if state is None:
            return

        table = self.query_one(DataTable)
        display, style = STATUS_STYLES.get(state.status, ("?", ""))
        styled_status = Text(display, style=style)

        pid_str = str(state.pid) if state.pid else "--"
        uptime_str = state.uptime
        restart_str = str(state.restart_count)

        table.update_cell(process_id, COL_STATUS, styled_status)
        table.update_cell(process_id, COL_PID, pid_str)
        table.update_cell(process_id, COL_UPTIME, uptime_str)
        table.update_cell(process_id, COL_RST, restart_str)

    def refresh_all(self) -> None:
        """Refresh all rows (called by timer for uptime updates)."""
        for pid in self._process_ids:
            self.update_process(pid)

    def get_selected_process_id(self) -> str | None:
        """Return the process_id of the currently selected row."""
        table = self.query_one(DataTable)
        if table.cursor_row is not None and table.cursor_row < len(self._process_ids):
            return self._process_ids[table.cursor_row]
        return None
