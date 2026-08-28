"""ConfigWatcher: inotify-based hot-reload for config files.

Watches the config directory for IN_CLOSE_WRITE events on hot-reloadable
YAML configs. Uses a 500ms debounce to coalesce rapid writes (e.g., editor
save + format) into a single reload callback.

Only control_config, alarms_config, and schedule_config support hot-reload.
system_config changes require a full restart (topology change).
"""

from __future__ import annotations

import asyncio
import logging
import select
from collections.abc import Awaitable, Callable
from pathlib import Path

from inotify_simple import INotify, flags

logger: logging.Logger = logging.getLogger(__name__)


class ConfigWatcher:
    """Watch config directory for hot-reloadable YAML file changes.

    Uses Linux inotify to detect IN_CLOSE_WRITE events. A 500ms debounce
    window coalesces rapid writes into a single callback invocation.

    Args:
        config_dir: Path to the config directory to watch.
        on_change: Async callback receiving the config name (stem) on change.
    """

    HOT_RELOAD_CONFIGS: set[str] = {
        "control_config",
        "alarms_config",
        "schedule_config",
    }

    DEBOUNCE_S: float = 0.5

    def __init__(
        self,
        config_dir: Path,
        on_change: Callable[[str], Awaitable[None]],
    ) -> None:
        self._config_dir: Path = config_dir
        self._on_change: Callable[[str], Awaitable[None]] = on_change
        self._debounce_tasks: dict[str, asyncio.Task] = {}
        self._inotify: INotify | None = None

    def stop(self) -> None:
        """Close inotify fd to unblock the watch loop."""
        if self._inotify is not None:
            try:
                self._inotify.close()
            except OSError:
                pass
            self._inotify = None

    async def watch_loop(self) -> None:
        """Run the inotify watch loop. Blocks until cancelled.

        Uses asyncio loop.add_reader for non-blocking inotify event reading.
        """
        self._inotify = INotify()
        watch_flags = flags.CLOSE_WRITE
        self._inotify.add_watch(str(self._config_dir), watch_flags)

        loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
        fd: int = self._inotify.fileno()

        logger.info("ConfigWatcher started on %s", self._config_dir)

        stop_event: asyncio.Event = asyncio.Event()

        def _on_readable() -> None:
            """Called when inotify fd is readable."""
            if self._inotify is None:
                return
            try:
                events = self._inotify.read()
            except OSError:
                stop_event.set()
                return

            for event in events:
                name: str = event.name
                if not name:
                    continue

                # Skip dot-prefixed temp files (editor swap files)
                if name.startswith("."):
                    continue

                # Only process .yaml files
                if not name.endswith(".yaml"):
                    continue

                # Extract stem (e.g., "control_config" from "control_config.yaml")
                stem: str = Path(name).stem

                # Only hot-reload allowed configs
                if stem not in self.HOT_RELOAD_CONFIGS:
                    if stem == "system_config":
                        logger.warning(
                            "system_config.yaml changed but hot-reload "
                            "is not supported (topology change requires restart)"
                        )
                    continue

                logger.info(
                    "Detected change in %s, scheduling reload (debounce %.1fs)",
                    name,
                    self.DEBOUNCE_S,
                )

                # Cancel any pending debounce for this config
                if stem in self._debounce_tasks:
                    self._debounce_tasks[stem].cancel()

                # Schedule new debounce task
                self._debounce_tasks[stem] = asyncio.create_task(
                    self._debounced_callback(stem)
                )

        try:
            loop.add_reader(fd, _on_readable)
            try:
                await stop_event.wait()
            except asyncio.CancelledError:
                pass
        finally:
            try:
                loop.remove_reader(fd)
            except (ValueError, OSError):
                pass
            # Cancel pending debounce tasks
            for task in self._debounce_tasks.values():
                task.cancel()
            self.stop()
            logger.info("ConfigWatcher stopped")

    async def _debounced_callback(self, name: str) -> None:
        """Wait for debounce period, then invoke the on_change callback.

        Args:
            name: Config name (stem) to pass to callback.
        """
        await asyncio.sleep(self.DEBOUNCE_S)
        logger.info("Debounce complete for %s, invoking reload", name)
        try:
            await self._on_change(name)
        except Exception:
            logger.exception("Error in on_change callback for %s", name)
        finally:
            # Clean up completed task reference
            self._debounce_tasks.pop(name, None)
