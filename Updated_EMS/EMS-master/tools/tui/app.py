"""EMS Developer TUI — Textual application."""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Static

from tools.tui.models import ProcessStatus, TuiConfig
from tools.tui.process_manager import ProcessManager
from tools.tui.widgets.error_modal import ErrorModal
from tools.tui.widgets.log_viewer import LogViewer
from tools.tui.widgets.process_table import ProcessSelected, ProcessTable


class EmsDevTui(App):
    """EMS Developer TUI — process manager for simulator and services."""

    TITLE = "EMS Dev TUI"

    CSS = """
    Screen {
        layout: vertical;
    }
    #profile-bar {
        height: 1;
        background: $primary;
        color: $text;
        padding: 0 2;
        text-align: right;
    }
    """

    BINDINGS = [
        Binding("a", "start_all", "Start All", priority=True),
        Binding("x", "stop_all", "Stop All", priority=True),
        Binding("s", "start_selected", "Start", priority=True),
        Binding("k", "stop_selected", "Stop", priority=True),
        Binding("r", "restart_selected", "Restart", priority=True),
        Binding("l", "toggle_log", "Log", priority=True),
        Binding("plus", "grow_log", "+Log", priority=True),
        Binding("minus", "shrink_log", "-Log", priority=True),
        Binding("p", "cycle_profile", "Profile", priority=True),
        Binding("enter", "show_error", "Details", show=False, priority=True),
        Binding("q", "quit_app", "Quit", priority=True),
    ]

    def __init__(self, profile: str = "residential", config_path: Path | None = None) -> None:
        super().__init__()
        self._profile: str = profile
        self._config_path: Path = config_path or (
            Path(__file__).parent / "processes.yaml"
        )
        self._working_dir: Path = Path(__file__).resolve().parents[2]  # project root
        self._tui_config: TuiConfig = TuiConfig.from_yaml(self._config_path)
        self._manager: ProcessManager | None = None
        self._log_visible: bool = True

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(f"Profile: {self._profile}", id="profile-bar")

        process_ids = [
            p.id for phase in sorted(self._tui_config.phases, key=lambda ph: ph.order)
            for p in phase.processes
        ]
        manager = ProcessManager(
            config=self._tui_config,
            profile=self._profile,
            working_dir=self._working_dir,
        )
        self._manager = manager

        table = ProcessTable(process_ids, manager.states)
        table.set_phase_resolver(manager.get_phase_for_process)
        yield table
        yield LogViewer()
        yield Footer()

    def on_mount(self) -> None:
        assert self._manager is not None
        self._manager._on_state_change = self._on_state_change
        self._manager._on_log_line = self._on_log_line
        self.set_interval(1.0, self._refresh_table)
        self._check_binaries()

        # Auto-select first process for log viewer
        process_ids = self._manager.get_all_process_ids()
        if process_ids:
            first = process_ids[0]
            state = self._manager.states.get(first)
            if state:
                log_viewer = self.query_one(LogViewer)
                log_viewer.show_process(first, state)

    def _check_binaries(self) -> None:
        """Warn about missing C binaries."""
        for pid, state in self._manager.states.items():
            cmd = state.config.command
            if cmd.startswith("./build/"):
                binary = cmd.split()[0]
                full_path = self._working_dir / binary
                if not full_path.exists():
                    self._manager._append_log(
                        pid,
                        f"[tui] WARNING: Binary not found: {binary} — run 'make build' first",
                    )

    def _on_state_change(
        self, process_id: str, old: ProcessStatus, new: ProcessStatus
    ) -> None:
        """Callback from ProcessManager when a process changes state."""
        try:
            table = self.query_one(ProcessTable)
            table.update_process(process_id)
        except Exception:
            pass

    def _on_log_line(self, process_id: str, line: str) -> None:
        """Callback from ProcessManager when a log line arrives."""
        try:
            log_viewer = self.query_one(LogViewer)
            log_viewer.append_line(process_id, line)
        except Exception:
            pass

    def _refresh_table(self) -> None:
        """Timer callback to refresh uptime column."""
        try:
            table = self.query_one(ProcessTable)
            table.refresh_all()
        except Exception:
            pass

    def on_process_selected(self, event: ProcessSelected) -> None:
        """When user moves cursor in the process table."""
        state = self._manager.states.get(event.process_id)
        if state:
            log_viewer = self.query_one(LogViewer)
            log_viewer.show_process(event.process_id, state)

    async def action_start_all(self) -> None:
        """Start all processes in phase order."""
        self.notify("Starting all processes...")
        await self._manager.start_all()

    async def action_stop_all(self) -> None:
        """Stop all processes in reverse order."""
        self.notify("Stopping all processes...")
        await self._manager.stop_all()

    async def action_start_selected(self) -> None:
        """Start the selected process."""
        pid = self._get_selected_pid()
        if pid:
            await self._manager.start_process(pid)

    async def action_stop_selected(self) -> None:
        """Stop the selected process."""
        pid = self._get_selected_pid()
        if pid:
            await self._manager.stop_process(pid)

    async def action_restart_selected(self) -> None:
        """Restart the selected process."""
        pid = self._get_selected_pid()
        if pid:
            await self._manager.restart_process(pid)

    def action_toggle_log(self) -> None:
        """Toggle log panel visibility."""
        log_viewer = self.query_one(LogViewer)
        self._log_visible = not self._log_visible
        log_viewer.display = self._log_visible

    def action_grow_log(self) -> None:
        """Increase log panel height."""
        self.query_one(LogViewer).grow()

    def action_shrink_log(self) -> None:
        """Decrease log panel height."""
        self.query_one(LogViewer).shrink()

    async def action_cycle_profile(self) -> None:
        """Cycle through available profiles, restarting affected processes."""
        profiles = self._tui_config.profiles
        idx = profiles.index(self._profile) if self._profile in profiles else -1
        self._profile = profiles[(idx + 1) % len(profiles)]

        self.query_one("#profile-bar", Static).update(f"Profile: {self._profile}")
        self._manager.profile = self._profile

        affected = self._manager.get_profile_affected_ids()
        if affected:
            self.notify(
                f"Profile: {self._profile} — restarting {len(affected)} affected processes"
            )
            for pid in affected:
                await self._manager.restart_process(pid)
        else:
            self.notify(f"Profile: {self._profile}")

    def action_show_error(self) -> None:
        """Show error modal for crashed process."""
        pid = self._get_selected_pid()
        if pid is None:
            return
        state = self._manager.states.get(pid)
        if state is None or state.status != ProcessStatus.CRASHED:
            return

        def handle_result(restart: bool | None) -> None:
            if restart:
                self.run_worker(self._manager.restart_process(pid), exit_on_error=False)

        self.push_screen(ErrorModal(pid, state), handle_result)

    async def action_quit_app(self) -> None:
        """Clean shutdown — stop all processes, then exit."""
        self.notify("Shutting down...")
        await self._manager.cleanup()
        self.exit()

    def _get_selected_pid(self) -> str | None:
        """Get the process_id of the currently selected row."""
        table = self.query_one(ProcessTable)
        return table.get_selected_process_id()
