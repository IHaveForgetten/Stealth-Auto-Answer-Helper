"""全局热键 Ctrl+Alt+Y 与 Esc 中止，基于 keyboard 钩子。"""

from __future__ import annotations

import threading
from typing import Callable

import keyboard

from logging_setup import get_logger

log = get_logger()

MAIN_HOTKEY = "ctrl+alt+y"
ABORT_HOTKEY = "esc"


class HotkeyManager:
    def __init__(self) -> None:
        self._handles: list = []

    def register(self, on_trigger: Callable[[], None], on_abort: Callable[[], None]) -> None:
        self._handles.append(
            keyboard.add_hotkey(MAIN_HOTKEY, lambda: self._safe(on_trigger), suppress=False)
        )
        self._handles.append(
            keyboard.add_hotkey(ABORT_HOTKEY, lambda: self._safe(on_abort), suppress=False)
        )
        log.info("已注册热键 %s，中止键 %s", MAIN_HOTKEY.upper(), ABORT_HOTKEY.upper())

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
