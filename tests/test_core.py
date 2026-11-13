"""离线自测：不触碰真实键盘鼠标，用假后端验证逻辑。"""

from __future__ import annotations

import random
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai_client import parse_answer  # noqa: E402
from humanizer import Humanizer  # noqa: E402
from input_engine import Interrupted, move_mouse_smooth, type_text  # noqa: E402


class FakeKeyboard:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.down: set[str] = set()

    def press(self, key: str) -> None:
        self.down.add(key)
        self.events.append(f"down:{key}")

    def release(self, key: str) -> None:
        self.down.discard(key)
        self.events.append(f"up:{key}")

    def write(self, text: str) -> None:
        for ch in text:
            self.events.append("bs" if ch == "\b" else ch)

    @property
    def typed(self) -> str:
        """按 down 事件还原真正落在输入框里的文本（shift 按下时输出大写/符号）。"""
        out: list[str] = []
        shift = False
        for ev in self.events:
            if ev == "down:shift":
                shift = True
            elif ev == "up:shift":
                shift = False
            elif ev == "down:backspace":
                if out:
                    out.pop()
            elif ev.startswith("down:"):
                key = ev[5:]
                if len(key) != 1:
                    continue  # 修饰键或特殊键，不进文本
                plain = _PLAIN.get(key, key)
                if shift and key in _SHIFTED_CHARS:
                    out.append(_SHIFTED_CHARS[key])
                else:
                    out.append(plain.upper() if shift else plain)
        return "".join(out)


class FakeMouse:
    def __init__(self, x: int = 100, y: int = 100) -> None:
        self.x, self.y = x, y
        self.track: list[tuple[int, int]] = []

    def position(self):
        return self.x, self.y

    def moveTo(self, x, y):
        self.x, self.y = x, y
        self.track.append((x, y))


def _fast_hz(**kw) -> Humanizer:
    return Humanizer(median_delay=0.001, sigma=0.2, rng=random.Random(1), **kw)


# 未按 shift 的基础键会打出的字符（用于把按键序列还原成文本）
_PLAIN = {
    "1": "1", "2": "2", "3": "3", "4": "4", "5": "5", "6": "6", "7": "7",
    "8": "8", "9": "9", "0": "0", "-": "-", "=": "=", "[": "[", "]": "]",
    "\\": "\\", ";": ";", "'": "'", ",": ",", ".": ".", "/": "/", "`": "`",
}
_SHIFTED_CHARS = {
    "1": "!", "2": "@", "3": "#", "4": "$", "5": "%", "6": "^", "7": "&",
    "8": "*", "9": "(", "0": ")", "-": "_", "=": "+", "[": "{", "]": "}",
    "\\": "|", ";": ":", "'": '"', ",": "<", ".": ">", "/": "?",
}


class TestHumanizer(unittest.TestCase):
    def test_delays_are_varied_and_in_range(self) -> None:
        hz = Humanizer(median_delay=0.085, rng=random.Random(7))
        delays = [hz.inter_key_delay() for _ in range(2000)]
        self.assertTrue(all(0.012 <= d <= 1.6 for d in delays))
        self.assertGreater(len(set(round(d, 4) for d in delays)), 1000)
        mean = sum(delays) / len(delays)
        self.assertGreater(mean, 0.07)
        self.assertLess(mean, 0.2)

    def test_plan_preserves_text(self) -> None:
        hz = Humanizer(typo_rate=0.3, rng=random.Random(3))
        text = "The quick brown fox jumps over 13 lazy dogs. 完了"
        plan = hz.plan(text)
        self.assertEqual("".join(k.text for k in plan), text)
        self.assertTrue(any(k.typo for k in plan))


class TestTyping(unittest.TestCase):
    def test_types_exactly_and_fixes_typos(self) -> None:
        kb = FakeKeyboard()
        text = "Answer 42: the value is exponential."
        type_text(text, _fast_hz(typo_rate=0.4), threading.Event(), backend=kb)
        self.assertEqual(kb.typed, text)
        self.assertIn("down:backspace", kb.events, "应出现错字回删")
        self.assertNotIn("shift", kb.down)
        # 每个字符都必须是 down/up 配对，不能挂在按下状态
        self.assertEqual(kb.down, set())
        self.assertFalse(any(e in ("\n", "\r") for e in kb.events), "不得按回车")

    def test_shift_for_uppercase_and_symbols(self) -> None:
        kb = FakeKeyboard()
        type_text("Ab!", _fast_hz(typo_rate=0), threading.Event(), backend=kb)
        self.assertEqual(kb.events.count("down:shift"), 2)  # A 和 !
        self.assertEqual(kb.events.count("up:shift"), 2)
        self.assertEqual(kb.typed, "Ab!")

    def test_stop_aborts_promptly(self) -> None:
        stop = threading.Event()
        kb = FakeKeyboard()
        threading.Timer(0.05, stop.set).start()
        t0 = time.perf_counter()
        with self.assertRaises(Interrupted):
            type_text("x" * 5000, Humanizer(median_delay=0.01), stop, backend=kb)
        self.assertLess(time.perf_counter() - t0, 1.0)
        self.assertEqual(kb.down, set(), "中止时不能留下按住的键")


class TestMouse(unittest.TestCase):
    def test_curve_is_smooth_and_reaches_target(self) -> None:
        mouse = FakeMouse(0, 0)
        hz = Humanizer(rng=random.Random(5))
        move_mouse_smooth(600, 400, hz, threading.Event(), backend=mouse)
        self.assertEqual(mouse.track[-1], (600, 400))
        self.assertGreater(len(mouse.track), 20)
        steps = [(abs(b[0] - a[0]), abs(b[1] - a[1])) for a, b in zip(mouse.track, mouse.track[1:])]
        self.assertLess(max(max(s) for s in steps), 80, "单步位移过大，不自然")

    def test_stop_aborts_mouse(self) -> None:
        stop = threading.Event()
        stop.set()
        with self.assertRaises(Interrupted):
            move_mouse_smooth(900, 900, Humanizer(), stop, backend=FakeMouse())


class TestPyAutoGUIBackend(unittest.TestCase):
    """真实 pyautogui 只有 keyDown/keyUp；这组测试防止后端适配再漏掉。"""

    def test_adapter_uses_keydown_keyup(self) -> None:
        import input_engine

        calls: list[tuple[str, str]] = []

        class FakePyAutoGUI:
            def keyDown(self, key):
                calls.append(("down", key))

            def keyUp(self, key):
                calls.append(("up", key))

            def write(self, text):
                calls.append(("write", text))

        adapter = input_engine.PyAutoGUIKeyboard()
        original = input_engine.pyautogui
        input_engine.pyautogui = FakePyAutoGUI()
        try:
            adapter.press("a")
            adapter.release("a")
        finally:
            input_engine.pyautogui = original
        self.assertEqual(calls, [("down", "a"), ("up", "a")])

    def test_non_pyautogui_backend_passthrough(self) -> None:
        import input_engine

        fake = FakeKeyboard()
        self.assertIs(input_engine._adapt_backend(fake), fake)

    def test_default_backend_is_adapted(self) -> None:
        import input_engine

        if input_engine.pyautogui is None:
            self.skipTest("pyautogui 不可用")
        kb = input_engine._adapt_backend(input_engine.pyautogui)
        self.assertIsInstance(kb, input_engine.PyAutoGUIKeyboard)


class TestParsing(unittest.TestCase):
    def test_plain_json(self) -> None:
        a = parse_answer(
            '{"type":"choice","question":"1+1=?","answer":"B","choice":{"label":"B","text":"2"},'
            '"target":{"x":120.4,"y":340.9},"confidence":0.92}'
        )
        self.assertEqual(a.type, "choice")
        self.assertEqual(a.answer, "B")
        self.assertEqual(a.target, (120, 341))
        self.assertAlmostEqual(a.confidence, 0.92)

    def test_fenced_json_with_chatter(self) -> None:
        a = parse_answer('好的，答案如下：\n```json\n{"type":"text","answer":"答案是：42","confidence":0.8}\n```\n完毕')
        self.assertEqual(a.answer, "42")

    def test_leading_brace_before_fence(self) -> None:
        a = parse_answer('解析 { "type": "text", "answer": "x{y}z" } 结束')
        self.assertEqual(a.answer, "x{y}z")

    def test_non_json_fallback(self) -> None:
        a = parse_answer("正确答案：光合作用")
        self.assertEqual(a.type, "text")
        self.assertEqual(a.answer, "光合作用")
        self.assertLess(a.confidence, 0.5)

    def test_empty(self) -> None:
        self.assertEqual(parse_answer("").type, "unknown")

    def test_confidence_clamped(self) -> None:
        self.assertEqual(parse_answer('{"answer":"a","confidence":1.7}').confidence, 1.0)

    def test_current_blank_parsed(self) -> None:
        a = parse_answer(
            '{"type":"fill","current_blank":3,"blank_count":4,'
            '"answer":"90","blanks":["3","4","90","120"],"target":{"x":200,"y":260}}'
        )
        self.assertEqual(a.current_blank, 3)
        self.assertEqual(a.blank_count, 4)
        self.assertEqual(a.answer, "90")
        self.assertEqual(a.target, (200, 260))
        self.assertIn("第3空", a.summary())

    def test_answer_filled_from_blank_index(self) -> None:
        a = parse_answer('{"type":"fill","current_blank":2,"blank_count":4,"blanks":["3","4","90","120"]}')
        self.assertEqual(a.answer, "4")

    def test_multi_blank_without_index_defaults_to_first(self) -> None:
        a = parse_answer('{"type":"fill","blank_count":3,"blanks":["a","b","c"]}')
        self.assertEqual(a.current_blank, 1)
        self.assertEqual(a.answer, "a")

    def test_out_of_range_blank_index_is_ignored(self) -> None:
        a = parse_answer('{"type":"fill","current_blank":9,"blank_count":2,"blanks":["a","b"],"answer":"z"}')
        self.assertEqual(a.current_blank, 1)
        self.assertEqual(a.answer, "z")

    def test_string_numbers_tolerated(self) -> None:
        a = parse_answer('{"current_blank":"3","blank_count":"4"}')
        self.assertEqual(a.current_blank, 3)
        self.assertEqual(a.blank_count, 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
