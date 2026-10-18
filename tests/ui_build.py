"""用 Tk 无事件循环地构建界面，检查布局尺寸与控件引用是否正常。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tkinter as tk  # noqa: E402

from config import Config  # noqa: E402
from ui import App  # noqa: E402


def main() -> int:
    root = tk.Tk()
    app = App(root, Config())
    root.update_idletasks()
    root.update()
    print("window:", root.winfo_width(), "x", root.winfo_height())
    for child in root.winfo_children():
        print(" ", child.winfo_class(), child.winfo_width(), "x", child.winfo_height())

    # 配置收集与回填
    app.vars["base_url"].set("https://example.com/v1/")
    app.vars["model"].set("qwen-vl-max")
    app.vars["speed"].set("slow")
    app.vars["typo_rate"].set(9.0)
    cfg = app._collect()
    print("chat_url:", cfg.chat_url)
    print("model/speed/typo:", cfg.model, cfg.speed, round(cfg.typo_rate, 3))
    print("start_delay:", cfg.start_delay_min, cfg.start_delay_max)

    app.hotkeys.unregister()
    root.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
