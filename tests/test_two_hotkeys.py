"""两热键流程测试：Y 取答案、H 每次只输入一项，覆盖多空题逐空输入与重新取答案。

全部打桩，不触碰真实键鼠。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pyautogui  # noqa: E402
from PIL import Image  # noqa: E402

import pipeline  # noqa: E402
import screen  # noqa: E402
from ai_client import Answer  # noqa: E402
from config import Config  # noqa: E402

SHOT = screen.Screenshot(
    image=Image.new("RGB", (480, 270), "white"), scale=4.0, origin_x=0, origin_y=0,
    width=1920, height=1080,
)

rec: dict = {}


def _patch(answer: Answer | None, **cfg_kw):
    cfg = Config(
        base_url="http://stub", model="stub", api_key="stub",
        speed="fast", typo_rate=0.0, start_delay_min=0, start_delay_max=0, **cfg_kw,
    )
    orig = {
        "capture": pipeline.screen.capture,
        "ask": pipeline.ai_client.ask,
        "type": pipeline.type_text,
        "move": pipeline.move_mouse_smooth,
        "hotkey": pyautogui.hotkey,
        "press": pyautogui.press,
        "position": pyautogui.position,
        "sleep": time.sleep,
        "record": pipeline.storage.record,
    }
    rec.update(typed=[], hotkeys=[], clicks=0, records=[])
    pipeline.screen.capture = lambda max_side: SHOT
    pipeline.ai_client.ask = lambda *a, **k: answer
    pipeline.type_text = lambda text, hz, stop, backend=None: rec["typed"].append(text)
    pipeline.move_mouse_smooth = lambda *a, **k: rec.__setitem__("clicks", rec["clicks"] + 1)
    pyautogui.hotkey = lambda *keys: rec["hotkeys"].append("+".join(keys))
    pyautogui.press = lambda key: rec["hotkeys"].append(key)
    pyautogui.position = lambda: (500, 500)
    time.sleep = lambda s: None
    pipeline.storage.record = lambda *a, **k: rec["records"].append(k)
    return cfg, orig


def _restore(orig) -> None:
    pipeline.screen.capture = orig["capture"]
    pipeline.ai_client.ask = orig["ask"]
    pipeline.type_text = orig["type"]
    pipeline.move_mouse_smooth = orig["move"]
    pyautogui.hotkey = orig["hotkey"]
    pyautogui.press = orig["press"]
    pyautogui.position = orig["position"]
    time.sleep = orig["sleep"]
    pipeline.storage.record = orig["record"]


def _wait(pipe: pipeline.Pipeline) -> None:
    end = time.time() + 10
    while pipe.running and time.time() < end:
        time.sleep(0.01)


def main() -> int:
    failures: list[str] = []

    def check(name: str, cond: bool, detail="") -> None:
        print(("  通过 " if cond else "  失败 ") + name + (f"  {detail}" if not cond else ""))
        if not cond:
            failures.append(name)

    print("用例 1：四空题，Y 取答案后每次 H 只输入一项")
    answer = Answer(type="fill", blanks=["3", "4", "90", "120"], blank_count=4, confidence=0.9)
    cfg, orig = _patch(answer)
    statuses: list[str] = []
    pipe = pipeline.Pipeline(cfg, statuses.append)
    try:
        pipe.start_scan()
        _wait(pipe)
        check("H 之前没有任何输入", rec["typed"] == [], rec["typed"])
        check("取答案后有待输入项", pipe.has_pending(), statuses[-1])

        pipe.start_type()
        _wait(pipe)
        check("第一次 H 只输入 3", rec["typed"] == ["3"], rec["typed"])
        check("剩余 3 项", pipe.session and pipe.session.remaining == 3, statuses[-1])
        check("提示切到下一个空", any("下一个空" in s for s in statuses), statuses[-1])

        pipe.start_type()
        _wait(pipe)
        pipe.start_type()
        _wait(pipe)
        pipe.start_type()
        _wait(pipe)
        check("四次 H 依次输入 3,4,90,120", rec["typed"] == ["3", "4", "90", "120"], rec["typed"])
        check("没有自动点击/移动", rec["clicks"] == 0, rec["clicks"])
        check("全部完成后无剩余", not pipe.has_pending(), pipe.session)
        check("完成后 H 不会启动", not pipe.start_type())
    finally:
        _restore(orig)

    print("用例 2：剩余未输入时再次 Y 会丢弃旧答案")
    answer = Answer(type="fill", blanks=["a", "b", "c"], blank_count=3, confidence=0.9)
    cfg, orig = _patch(answer)
    pipe = pipeline.Pipeline(cfg, lambda s: None)
    try:
        pipe.start_scan()
        _wait(pipe)
        pipe.start_type()
        _wait(pipe)
        check("先输入了 a", rec["typed"] == ["a"], rec["typed"])
        pipe.start_scan()
        _wait(pipe)
        check("重新取答案后剩余项重置为 3", pipe.session and pipe.session.remaining == 3, pipe.session)
        check("重新取答案没有自动输入", rec["typed"] == ["a"], rec["typed"])
    finally:
        _restore(orig)

    print("用例 3：单空题，Y 后 H 输入一次即完成")
    cfg, orig = _patch(Answer(type="text", answer="41", confidence=0.9))
    pipe = pipeline.Pipeline(cfg, lambda s: None)
    try:
        pipe.start_scan()
        _wait(pipe)
        pipe.start_type()
        _wait(pipe)
        check("输入了 41", rec["typed"] == ["41"], rec["typed"])
        check("没有剩余", not pipe.has_pending())
        check("第二次 H 不再输入", not pipe.start_type())
    finally:
        _restore(orig)

    print("用例 4：只按 H 没有答案时不输入")
    cfg, orig = _patch(None)
    pipe = pipeline.Pipeline(cfg, lambda s: None)
    try:
        check("H 不会启动", not pipe.start_type())
        check("没有输入", rec["typed"] == [], rec["typed"])
    finally:
        _restore(orig)

    print("\n汇总：", "全部通过" if not failures else f"失败 {failures}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
