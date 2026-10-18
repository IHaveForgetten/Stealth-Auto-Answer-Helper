"""Autodati 入口。

使用：python main.py
热键：Ctrl+Alt+Y 截屏答题；Esc 中止。
"""

from __future__ import annotations

import ctypes
import sys

from logging_setup import get_logger


def _set_dpi_aware() -> None:
    """必须在创建任何窗口/截图之前调用，否则坐标会被系统缩放。"""
    try:
        # PROCESS_PER_MONITOR_DPI_AWARE = 2
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def _single_instance() -> bool:
    ctypes.windll.kernel32.CreateMutexW(None, False, "Global\\Autodati_SingleInstance")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        return False
    return True


def main() -> int:
    log = get_logger()
    if not _single_instance():
        ctypes.windll.user32.MessageBoxW(
            None, "Autodati 已经在运行了。", "Autodati", 0x40
        )
        return 0

    _set_dpi_aware()

    import tkinter as tk

    from config import load_config
    from ui import App

    try:
        cfg = load_config()
        root = tk.Tk()
    except Exception as exc:
        log.exception("启动失败")
        ctypes.windll.user32.MessageBoxW(None, f"启动失败：{exc}", "Autodati", 0x10)
        return 1

    app = App(root, cfg)
    log.info("界面已启动，等待热键 Ctrl+Alt+Y")
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            app.hotkeys.unregister()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
