"""真实环境冒烟：真实截图 + 真实鼠标移动（不按键、不点击）。"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from humanizer import Humanizer  # noqa: E402
from input_engine import move_mouse_smooth  # noqa: E402
from screen import capture  # noqa: E402


def main() -> int:
    t0 = time.perf_counter()
    shot = capture(1568)
    url = shot.to_data_url(82)
    print(f"截图 {shot.width}x{shot.height} -> {shot.image.width}x{shot.image.height}"
          f"，耗时 {time.perf_counter() - t0:.2f}s，data URL {len(url) / 1024:.0f} KB")

    hz = Humanizer()
    t0 = time.perf_counter()
    for target in [(200, 200), (1500, 800), (960, 540)]:
        move_mouse_smooth(*target, hz, threading.Event())
        print(f"移动到 {target} 完成，累计 {time.perf_counter() - t0:.2f}s")

    plan = hz.plan("The quick brown fox jumps over the lazy dog. 这是一个 42 字符的测试。")
    total = sum(k.delay_after + k.pause_after + (k.typo_fix_delay if k.typo else 0) for k in plan)
    typos = sum(1 for k in plan if k.typo)
    print(f"模拟 {len(plan)} 字符计划：预计 {total:.1f}s，错字 {typos} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
