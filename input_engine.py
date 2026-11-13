"""输入引擎：逐字符键入 + 平滑鼠标移动。

设计原则（与用户约定一致）：
- 只输入，不点击、不回车、不发送。
- 不抢焦点、不切窗口。
- 任何时刻可被外部 StopEvent 中断。
"""

from __future__ import annotations

import math
import random
import threading
import time
from typing import Callable, Protocol

from humanizer import Humanizer, Keystroke

try:
    import pyautogui

    pyautogui.FAILSAFE = False  # 由我们自己处理中止，不依赖移动到左上角
    pyautogui.PAUSE = 0
except Exception:  # pragma: no cover - 仅在无 GUI 环境下
    pyautogui = None


class _KeyboardBackend(Protocol):
    def press(self, key: str) -> None: ...
    def release(self, key: str) -> None: ...
    def write(self, text: str) -> None: ...


class Interrupted(Exception):
    """用户中止。"""


def _sleep(seconds: float, stop: threading.Event) -> None:
    if seconds > 0 and stop.wait(seconds):
        raise Interrupted()


# ------------------------------------------------------------------ 键盘


def _resolve_key(ch: str, backend: _KeyboardBackend) -> tuple[bool, str]:
    """返回 (是否需要 Shift, 基础键名)。"""
    from humanizer import _SHIFTED  # 复用同一张 Shift 映射表

    base = _SHIFTED.get(ch)
    if base is not None:
        return ch != base, base
    return False, ch


def type_text(
    text: str,
    humanizer: Humanizer,
    stop: threading.Event,
    backend: _KeyboardBackend | None = None,
    on_action: Callable[[str], None] | None = None,
) -> None:
    """按拟人节奏逐字符输入。中文等非 ASCII 字符走剪贴板兜底。"""
    kb = backend or pyautogui
    if kb is None:
        raise RuntimeError("没有可用的键盘后端")

    for ks in humanizer.plan(text):
        if stop.is_set():
            raise Interrupted()

        if ks.typo:
            _type_char(ks.typo, kb)
            _sleep(ks.typo_fix_delay, stop)
            _backspace(kb)
            _sleep(humanizer.rng.uniform(0.06, 0.14), stop)

        _type_char(ks.text, kb)
        if on_action:
            on_action(ks.text)
        _sleep(ks.delay_after, stop)
        if ks.pause_after:
            _sleep(ks.pause_after, stop)


def _backspace(kb: _KeyboardBackend) -> None:
    kb.press("backspace")
    kb.release("backspace")


def _type_char(ch: str, kb: _KeyboardBackend) -> None:
    if ch.isascii() or ch in "\t\n\r":
        shift, key = _resolve_key(ch, kb)
        if ch == "\n":
            kb.write("\n")
            return
        if shift:
            kb.press("shift")
        kb.press(key)
        kb.release(key)
        if shift:
            kb.release("shift")
        return

    # 非 ASCII：没有输入法上下文时无法逐字键入，退化为剪贴板粘贴
    if pyautogui is not None:
        import pyperclip

        pyperclip.copy(ch)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.05)
    else:
        raise RuntimeError(f"无法输入字符 {ch!r}")


# ------------------------------------------------------------------ 鼠标


def _ease_in_out_cubic(t: float) -> float:
    return 4 * t * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def _bezier(p0, p1, p2, p3, t: float) -> tuple[float, float]:
    u = 1 - t
    x = u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0]
    y = u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1]
    return x, y


def move_mouse_smooth(
    x: int,
    y: int,
    humanizer: Humanizer,
    stop: threading.Event,
    backend=None,
) -> None:
    """沿随机三次贝塞尔曲线移动鼠标，带缓入缓出与末端微调。"""
    mouse = backend or pyautogui
    if mouse is None:
        return

    start_x, start_y = mouse.position()
    dx, dy = x - start_x, y - start_y
    distance = math.hypot(dx, dy)
    if distance < 2:
        return

    duration = humanizer.mouse_duration(distance)
    steps = humanizer.mouse_steps(duration)

    rng = humanizer.rng
    # 控制点：沿路径两侧随机偏移，幅度随距离缩放，形成轻微弧线
    nx, ny = -dy / distance, dx / distance
    bend1 = rng.uniform(-0.10, 0.10) * distance
    bend2 = rng.uniform(-0.16, 0.16) * distance
    overshoot = rng.uniform(0.0, 0.035) * distance

    p0 = (start_x, start_y)
    p3 = (x + dx / distance * overshoot, y + dy / distance * overshoot)
    p1 = (start_x + dx / 3 + nx * bend1, start_y + dy / 3 + ny * bend1)
    p2 = (start_x + 2 * dx / 3 + nx * bend2, start_y + 2 * dy / 3 + ny * bend2)

    interval = duration / steps
    next_at = time.perf_counter()
    for i in range(1, steps + 1):
        if stop.is_set():
            raise Interrupted()
        t = _ease_in_out_cubic(i / steps)
        px, py = _bezier(p0, p1, p2, p3, t)
        mouse.moveTo(int(round(px)), int(round(py)))
        # 用累计时间轴推进，单帧抖动不会拖慢整体时长
        next_at += interval * rng.uniform(0.7, 1.3)
        remaining = next_at - time.perf_counter()
        if remaining > 0:
            stop.wait(remaining)
        else:
            # 落后时不要越欠越多，把时间轴拉回当前
            next_at = time.perf_counter()

    # 末端回正 + 一次微小修正
    mouse.moveTo(x, y)
    jx, jy, pause = humanizer.hover_jitter()
    time.sleep(pause)
    mouse.moveTo(x + jx, y + jy)
    time.sleep(rng.uniform(0.05, 0.12))
    mouse.moveTo(x, y)
