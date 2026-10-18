"""截图当前屏幕到临时 PNG，用于人工检查界面；不修改任何状态。"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tkinter as tk  # noqa: E402

from config import Config  # noqa: E402
from screen import capture  # noqa: E402
from ui import App  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "logs" / "ui_shot.png"


def main() -> int:
    tab = sys.argv[1] if len(sys.argv) > 1 else "api"
    root = tk.Tk()
    app = App(root, Config())
    root.geometry("560x620+40+40")
    if tab == "beh":
        app._select_tab(1)
    root.lift()
    root.attributes("-topmost", True)
    for _ in range(30):
        root.update()
        time.sleep(0.02)
    time.sleep(0.4)
    shot = capture(0)
    shot.image.crop((20, 20, 620, 700)).save(OUT)
    print("saved", OUT)
    app.hotkeys.unregister()
    root.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
