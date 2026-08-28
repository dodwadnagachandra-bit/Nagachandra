"""Device health state machine for comm_manager.

Tracks online/offline transitions with exponential backoff for offline devices.
Used by Modbus orchestrator to decide when to poll each device.
"""

from __future__ import annotations


class DeviceHealth:
    """Per-device health tracker with online/offline state and backoff.

    Devices start in offline (unknown) state. First successful poll triggers
    a recovery event. Consecutive failures beyond threshold trigger a fault
    event and switch to offline with exponential backoff.

    Attributes:
        BACKOFF_MIN_S: Minimum backoff interval (1 second).
        BACKOFF_MAX_S: Maximum backoff cap (30 seconds).
    """

    BACKOFF_MIN_S: float = 1.0
    BACKOFF_MAX_S: float = 30.0

    def __init__(
        self,
        device_id: str,
        offline_threshold: int = 3,
        poll_interval_ms: int = 1000,
    ) -> None:
        self.device_id: str = device_id
        self.online: bool = False
        self.consecutive_failures: int = 0
        self.offline_threshold: int = offline_threshold
        self.backoff_s: float = self.BACKOFF_MIN_S
        self.last_success_ms: int = 0
        self.last_poll_ms: int = -1  # -1 signals never polled
        self.went_offline_ms: int = 0
        self.poll_interval_ms: int = poll_interval_ms

    def record_success(self, now_ms: int) -> dict | None:
        """Record a successful communication with the device.

        Returns:
            Recovery event data dict if device was offline, else None.
        """
        self.consecutive_failures = 0

        if self.online:
            self.last_success_ms = now_ms
            return None

        # Transition: offline -> online (recovery)
        offline_duration_ms: int = now_ms - self.went_offline_ms
        self.online = True
        self.backoff_s = self.BACKOFF_MIN_S
        self.last_success_ms = now_ms

        return {
            "device_id": self.device_id,
            "offline_duration_ms": offline_duration_ms,
        }

    def record_failure(self, now_ms: int) -> dict | None:
        """Record a failed communication attempt.

        Returns:
            Fault event data dict if crossing offline threshold, else None.
        """
        self.consecutive_failures += 1

        if not self.online:
            # Already offline -- double backoff (capped)
            self.backoff_s = min(self.backoff_s * 2, self.BACKOFF_MAX_S)
            return None

        if self.consecutive_failures >= self.offline_threshold:
            # Transition: online -> offline (fault)
            self.online = False
            self.went_offline_ms = now_ms
            return {
                "device_id": self.device_id,
                "consecutive_failures": self.consecutive_failures,
            }

        return None

    def should_poll(self, now_ms: int) -> bool:
        """Check whether enough time has elapsed for next poll.

        Online devices use poll_interval_ms. Offline devices use
        backoff_s * 1000.
        """
        if self.last_poll_ms < 0:
            return True  # Never polled

        if self.online:
            return (now_ms - self.last_poll_ms) >= self.poll_interval_ms
        else:
            return (now_ms - self.last_poll_ms) >= (self.backoff_s * 1000)

    def record_poll(self, now_ms: int) -> None:
        """Record that a poll was issued at the given timestamp."""
        self.last_poll_ms = now_ms
