from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from .config import PluginConfig


# 退出态（exit-pending）保留时长，超时后彻底清理
EXIT_PENDING_TTL = 86400


@dataclass(slots=True)
class Session:
    active: bool
    """是否处于激活状态"""

    end: float | None = None
    """激活结束时间戳"""

    exit_ts: float | None = None
    """进入 exit-pending 的时间戳"""

    reason: str | None = None
    """退出原因：user / expire"""

    cooldown_end: float = 0.0
    """冷却结束时间戳"""


class SessionStore:
    """会话状态内存缓存（精简版）"""

    def __init__(self, config: PluginConfig):
        self.cfg = config
        self._data: dict[str, Session] = {}
        self._lock = asyncio.Lock()

    # ================= 内部 =================

    def _cleanup_one(self, key: str):
        """惰性清理：active超时转exit-pending，exit-pending超时时移除"""
        s = self._data.get(key)
        if not s:
            return

        now = time.time()

        if s.active and s.end is not None and s.end <= now:
            s.active = False
            s.exit_ts = now
            s.reason = "expire"
        elif not s.active and s.exit_ts is not None:
            if now - s.exit_ts > EXIT_PENDING_TTL:
                self._data.pop(key, None)

    # ================= API =================

    async def get(self, key: str) -> Session | None:
        async with self._lock:
            self._cleanup_one(key)
            return self._data.get(key)

    async def activate(self, key: str) -> None:
        """激活/重置会话（进入控制状态）"""
        async with self._lock:
            now = time.time()
            self._cleanup_one(key)

            self._data[key] = Session(
                active=True,
                end=now + self.cfg.state_duration,
                cooldown_end=now + self.cfg.cooldown_seconds,
            )

    async def deactivate(self, key: str) -> bool:
        """手动退出（转为exit-pending）"""
        async with self._lock:
            s = self._data.get(key)
            if not s or not s.active:
                return False
            s.active = False
            s.exit_ts = time.time()
            s.reason = "user"
            return True

    async def complete_exit(self, key: str) -> Session | None:
        """消费退出状态并移除会话（确保退出提示只注入一次）"""
        async with self._lock:
            s = self._data.get(key)
            if not s or s.active:
                return None
            return self._data.pop(key)

    async def check_cooldown(self, key: str) -> int:
        async with self._lock:
            s = self._data.get(key)
            if not s:
                return 0
            return max(0, int(s.cooldown_end - time.time()))
