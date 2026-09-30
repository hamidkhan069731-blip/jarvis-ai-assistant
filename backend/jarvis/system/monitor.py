"""Live system monitor.

Runs a lightweight async loop that pushes real metrics to the UI gauges and
raises *proactive* notifications when thresholds are crossed (low disk, sustained
high CPU, low battery). Proactive alerts respect the ``proactive_enabled`` and
are rate-limited so JARVIS is helpful, never naggy.
"""
from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable

from jarvis.config import settings
from jarvis.security.audit import get_logger
from jarvis.tools.system_tools import read_system_status

log = get_logger("system.monitor")

EmitFn = Callable[[dict], Awaitable[None]]

# threshold -> (min seconds between repeat alerts)
_ALERT_COOLDOWN = 600  # 10 minutes


class SystemMonitor:
    def __init__(self, emit: EmitFn, interval: float = 2.0) -> None:
        self.emit = emit
        self.interval = interval
        self._task: asyncio.Task | None = None
        self._high_cpu_since: float | None = None
        self._last_alert: dict[str, float] = {}

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())
            log.info("system monitor started")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    # ------------------------------------------------------------------ #
    async def _loop(self) -> None:
        while True:
            try:
                stats = await asyncio.to_thread(read_system_status)
                if stats.get("available"):
                    await self.emit({"type": "system_stats", "stats": stats})
                    await self._check_alerts(stats)
            except asyncio.CancelledError:
                raise
            except Exception:  # never let the monitor die on a transient error
                log.debug("monitor tick failed", exc_info=True)
            await asyncio.sleep(self.interval)

    async def _check_alerts(self, stats: dict) -> None:
        if not settings.get("proactive_enabled", True):
            self._high_cpu_since = None
            return
        now = time.time()

        # sustained high CPU (>90% for >30s)
        if stats.get("cpu_percent", 0) >= 90:
            self._high_cpu_since = self._high_cpu_since or now
            if now - self._high_cpu_since >= 30:
                await self._alert("cpu", "CPU has been above 90% for a while. "
                                  "Want me to show the top processes?")
        else:
            self._high_cpu_since = None

        # low disk (<10% free)
        if stats.get("disk_percent", 0) >= 90:
            await self._alert("disk", f"Disk is {stats['disk_percent']}% full "
                              f"({stats.get('disk_free_gb')} GB free). "
                              "I can find your largest files if you like.")

        # low battery (<15% and unplugged)
        if (stats.get("battery_percent", 100) <= 15
                and stats.get("battery_plugged") is False):
            await self._alert("battery", f"Battery is at {stats['battery_percent']}%. "
                              "You may want to plug in.")

    async def _alert(self, key: str, message: str) -> None:
        now = time.time()
        if now - self._last_alert.get(key, 0) < _ALERT_COOLDOWN:
            return
        self._last_alert[key] = now
        log.info("proactive alert (%s): %s", key, message)
        await self.emit({"type": "notification", "level": "warning",
                         "source": "monitor", "message": message})
