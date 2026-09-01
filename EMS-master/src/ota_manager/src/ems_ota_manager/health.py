"""OTA health checker: polls systemd services after a firmware update.

HealthChecker queries each required EMS service via systemctl is-active,
polling until all services are healthy or the timeout expires. A pluggable
check_fn is accepted for test isolation — tests pass a mock coroutine
instead of launching subprocesses.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger: logging.Logger = logging.getLogger(__name__)


class HealthChecker:
    """Polls EMS systemd services and reports aggregate health status.

    Used by OtaStateMachine.check_post_boot_health() after a reboot into
    a new firmware partition. If all services become active before the
    deadline the update is confirmed; otherwise the state machine rolls back.
    """

    def __init__(
        self,
        services: list[str],
        timeout_s: float = 300.0,
        poll_interval_s: float = 10.0,
        check_fn: Callable[[str], Awaitable[bool]] | None = None,
    ) -> None:
        """Initialise the health checker.

        Args:
            services: List of systemd service unit names to check
                (without the .service suffix).
            timeout_s: Maximum total time to wait for all services to
                become healthy, in seconds.
            poll_interval_s: Interval between consecutive polling rounds,
                in seconds.
            check_fn: Optional coroutine factory ``async (service_name) -> bool``.
                If provided, replaces the subprocess-based systemctl check.
                Intended for unit tests.
        """
        self._services: list[str] = services
        self._timeout_s: float = timeout_s
        self._poll_interval_s: float = poll_interval_s
        self._check_fn: Callable[[str], Awaitable[bool]] | None = check_fn

    async def _is_service_active(self, service: str) -> bool:
        """Check whether a single systemd service is active.

        Args:
            service: Service unit name without suffix (e.g. "safety_manager").

        Returns:
            True if the service reports "active", False otherwise.
        """
        if self._check_fn is not None:
            return await self._check_fn(service)

        proc: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
            "systemctl",
            "is-active",
            f"{service}.service",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, _ = await proc.communicate()
        status: str = stdout_bytes.decode().strip()
        return status == "active"

    async def check_services_active(self) -> tuple[bool, list[str]]:
        """Check all configured services in a single pass.

        Returns:
            A tuple ``(all_active, failed_services)`` where ``all_active``
            is True only when every service returned "active", and
            ``failed_services`` lists the names of services that failed.
        """
        failed: list[str] = []
        for svc in self._services:
            active: bool = await self._is_service_active(svc)
            if not active:
                failed.append(svc)
        all_active: bool = len(failed) == 0
        return all_active, failed

    async def run_health_check(self) -> bool:
        """Poll services until all are healthy or timeout expires.

        Polls every ``poll_interval_s`` seconds. Returns as soon as all
        services are active. If the deadline passes before all services
        become active, returns False.

        Returns:
            True if all services became active within the timeout, False
            if the timeout expired with at least one service still unhealthy.
        """
        deadline: float = asyncio.get_event_loop().time() + self._timeout_s
        while True:
            all_active, failed = await self.check_services_active()
            if all_active:
                logger.info("Health check passed — all services active")
                return True
            remaining: float = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                logger.warning(
                    "Health check timed out after %.0fs — failed: %s",
                    self._timeout_s,
                    failed,
                )
                return False
            sleep_for: float = min(self._poll_interval_s, remaining)
            logger.debug(
                "Health check: %d service(s) not yet active (%s), retry in %.1fs",
                len(failed),
                failed,
                sleep_for,
            )
            await asyncio.sleep(sleep_for)
