"""真实后端端到端验证：记事本 + 真实 pyautogui 按键。

文字题部分走完整 Pipeline（真实截图、真实键盘输入，AI 用打桩返回值），
输入完成后从记事本复制回来比对。
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pyautogui  # noqa: E402

import ai_client  # noqa: E402
import pipeline  # noqa: E402
from config import Config  # noqa: E402

EXPECT_TEXT = "Answer 42: a<b>c & Value_1"
EXPECT_CHOICE = "B"


def _active_window() -> str:
    try:
        return pyautogui.getActiveWindowTitle() or ""
    except Exception:
        return ""


def _wait_active(substr: str, timeout: float = 6.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if substr in _active_window():
            return True
        time.sleep(0.1)
    return False


def _run_pipeline(answer: ai_client.Answer, cfg: Config) -> tuple[bool, list[str]]:
    statuses: list[str] = []
    pipe = pipeline.Pipeline(cfg, statuses.append)
    cfg.api_key = "stub"  # Pipeline 只检查是否为空，真正调用被下面打桩替换
    original = ai_client.ask
    ai_client.ask = lambda *a, **k: answer
    try:
        pipe.start_async()
        end = time.time() + 40
        while pipe.running and time.time() < end:
            time.sleep(0.05)
    finally:
        ai_client.ask = original
    ok = any(s.startswith("完成") for s in statuses)
    return ok, statuses


def main() -> int:
    pyautogui.PAUSE = 0
    cfg = Config(speed="fast", typo_rate=0.0, start_delay_min=0, start_delay_max=0)
    results: list[tuple[str, bool]] = []

    # ---- 用例 1：文字题，应逐字输入
    proc = subprocess.Popen(["notepad.exe"])
    try:
        if not _wait_active("记事本"):
            print("记事本未获得焦点，中止")
            return 2
        pyautogui.hotkey("ctrl", "a")
        pyautogui.press("delete")
        time.sleep(0.3)

        ok, statuses = _run_pipeline(ai_client.Answer(type="text", answer=EXPECT_TEXT, confidence=0.9), cfg)
        print("文字题状态：", statuses)
        time.sleep(0.4)

        # 全选复制，从剪贴板读回（避开保存对话框的焦点问题）
        pyautogui.hotkey("ctrl", "a")
        pyautogui.hotkey("ctrl", "c")
        time.sleep(0.4)
    finally:
        pyautogui.hotkey("alt", "f4")
        time.sleep(0.5)
        pyautogui.press("n")
        try:
            proc.wait(timeout=3)
        except Exception:
            proc.kill()

    import pyperclip

    got = pyperclip.paste().strip()
    print("文字题 期望：", repr(EXPECT_TEXT))
    print("文字题 实际：", repr(got))
    results.append(("文字题逐字输入", ok and got == EXPECT_TEXT))

    # ---- 用例 2：选择题，应只移动鼠标、不输入
    before = pyautogui.position()
    ok, statuses = _run_pipeline(
        ai_client.Answer(
            type="choice", answer=EXPECT_CHOICE,
            choice_label="B", choice_text="2",
            target=(900, 500), confidence=0.9,
        ),
        cfg,
    )
    after = pyautogui.position()
    moved = abs(after.x - before.x) > 5 or abs(after.y - before.y) > 5
    print("选择题状态：", statuses)
    print("鼠标：", before, "->", after)
    results.append(("选择题移动鼠标", ok and moved))

    failed = [name for name, ok_ in results if not ok_]
    print("\n汇总：", "全部通过" if not failed else f"失败 {failed}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
