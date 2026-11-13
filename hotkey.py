"""全局热键：Ctrl+Alt+Y 截屏取答案，Ctrl+Alt+H 输入一个答案，Esc 中止。"""

from __future__ import annotations

import threading
from typing import Callable

import keyboard

from logging_setup import get_logger

log = get_logger()

SCAN_HOTKEY = "ctrl+alt+y"
TYPE_HOTKEY = "ctrl+alt+h"
ABORT_HOTKEY = "esc"

# 兼容旧名字：截屏热键
MAIN_HOTKEY = SCAN_HOTKEY


def pretty(hotkey: str) -> str:
    return hotkey.upper().replace("+", " + ")


class HotkeyManager:
    def __init__(self) -> None:
        self._handles: list = []

    def register(
        self,
        on_scan: Callable[[], None],
        on_type: Callable[[], None],
        on_abort: Callable[[], None],
    ) -> None:
        for hotkey, fn in (
            (SCAN_HOTKEY, on_scan),
            (TYPE_HOTKEY, on_type),
            (ABORT_HOTKEY, on_abort),
        ):
            self._handles.append(
                keyboard.add_hotkey(hotkey, lambda f=fn: self._safe(f), suppress=False)
            )
        log.info(
            "已注册热键：%s 取答案，%s 输入答案，%s 中止",
            pretty(SCAN_HOTKEY), pretty(TYPE_HOTKEY), pretty(ABORT_HOTKEY),
        )

    @staticmethod
    def _safe(fn: Callable[[], None]) -> None:
        # 热键回调运行在钩子线程中，不能在那里做耗时操作
        threading.Thread(target=_guard, args=(fn,), daemon=True, name="hotkey-cb").start()

    def unregister(self) -> None:
        for handle in self._handles:
            try:
                keyboard.remove_hotkey(handle)
            except (KeyError, ValueError):
                pass
        self._handles.clear()


def _guard(fn: Callable[[], None]) -> None:
    try:
        fn()
    except Exception:
        log.exception("热键回调异常")
