"""拟人化参数：击键间隔、停顿、错字、鼠标轨迹噪声。

所有分布都是逐次抽样，不缓存、不复用序列，避免出现固定节奏。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

# QWERTY 相邻键表，用于生成"手滑"错字
_NEIGHBORS = {
    "q": "wa", "w": "qes", "e": "wrd", "r": "etf", "t": "ryg", "y": "tuh",
    "u": "yij", "i": "uok", "o": "ipl", "p": "ol",
    "a": "qsz", "s": "awdx", "d": "sefc", "f": "drgv", "g": "fthb",
    "h": "gyjn", "j": "hukm", "k": "jil", "l": "kop",
    "z": "asx", "x": "zsdc", "c": "xdfv", "v": "cfgb", "b": "vghn",
    "n": "bhjm", "m": "njk",
    "1": "2q", "2": "13w", "3": "24e", "4": "35r", "5": "46t",
    "6": "57y", "7": "68u", "8": "79i", "9": "80o", "0": "9p",
}

# 需要配合 Shift 的符号 -> 基础键
_SHIFTED = {
    "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6",
    "&": "7", "*": "8", "(": "9", ")": "0", "_": "-", "+": "=",
    "{": "[", "}": "]", "|": "\\", ":": ";", '"': "'", "<": ",", ">": ".", "?": "/",
}
for _c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    _SHIFTED[_c] = _c.lower()


@dataclass
class Keystroke:
    """一次击键计划：先按 text，再决定是否打错后回删。"""

    text: str
    delay_after: float
    typo: str | None = None
    typo_fix_delay: float = 0.0
    pause_after: float = 0.0


class Humanizer:
    def __init__(
        self,
        median_delay: float = 0.085,
        sigma: float = 0.55,
        typo_rate: float = 0.06,
        rng: random.Random | None = None,
    ) -> None:
        self.median_delay = median_delay
        self.sigma = sigma
        self.typo_rate = max(0.0, min(0.35, typo_rate))
        self.rng = rng or random.Random()

    # ------------------------------------------------------------ 基础分布

    def inter_key_delay(self) -> float:
        """人打字很少匀速：对数正态 + 偶尔的长停顿。"""
        d = self.rng.lognormvariate(math.log(self.median_delay), self.sigma)
        d = min(max(d, 0.012), 0.9)
        if self.rng.random() < 0.03:
            d += self.rng.uniform(0.25, 0.7)
        return d

    def word_pause(self) -> float:
        return self.rng.uniform(0.12, 0.42)

    def sentence_pause(self) -> float:
        return self.rng.uniform(0.35, 1.1)

    def think_delay(self) -> float:
        return self.rng.uniform(0.4, 1.2)

    # ------------------------------------------------------------ 计划生成

    def plan(self, text: str) -> list[Keystroke]:
        plan: list[Keystroke] = []
        chars_per_pause = 0
        pause_threshold = self.rng.randint(9, 16)

        for ch in text:
            ks = Keystroke(text=ch, delay_after=self.inter_key_delay())

            if self._typoable(ch) and self.rng.random() < self.typo_rate:
                wrong = self._neighbor(ch)
                if wrong:
                    ks.typo = wrong
                    ks.typo_fix_delay = self.rng.uniform(0.14, 0.42)

            if ch in " \t":
                ks.pause_after = self.word_pause()
                chars_per_pause = 0
            elif ch in ".!?。！？；;":
                ks.pause_after = self.sentence_pause()
                chars_per_pause = 0
            else:
                chars_per_pause += 1
                if chars_per_pause >= pause_threshold:
                    ks.pause_after = self.rng.uniform(0.2, 0.6)
                    chars_per_pause = 0
                    pause_threshold = self.rng.randint(9, 16)

            plan.append(ks)

        # 有时会先犹豫一下再开始
        if plan and self.rng.random() < 0.6:
            plan[0].delay_after += self.rng.uniform(0.05, 0.2)
        return plan

    def _typoable(self, ch: str) -> bool:
        return ch.isalnum() or ch in _SHIFTED

    def _neighbor(self, ch: str) -> str | None:
        base = _SHIFTED.get(ch, ch)
        opts = _NEIGHBORS.get(base.lower(), "")
        if not opts:
            return None
        wrong = self.rng.choice(opts)
        if ch.isupper():
            wrong = wrong.upper()
        return wrong if wrong != ch else None

    # ------------------------------------------------------------ 鼠标

    def mouse_duration(self, distance_px: float) -> float:
        """Fitts 定律的近似：距离越远耗时越长，但边际递减，并加噪声。"""
        base = 0.16 + 0.13 * math.log2(1 + distance_px / 12.0)
        return max(0.16, base * self.rng.uniform(0.85, 1.2))

    def mouse_steps(self, duration: float) -> int:
        # 约 60–90 步/秒，接近真实鼠标的 125Hz 采样但略慢
        return max(24, int(duration * self.rng.uniform(55, 80)))

    def approach_duration(self) -> float:
        return self.rng.uniform(0.25, 0.55)

    def hover_jitter(self) -> tuple[int, int, float]:
        """到达后的小幅修正：dx, dy, 停顿。"""
        return self.rng.choice([-1, 1]) * self.rng.randint(1, 2), self.rng.randint(-1, 1), self.rng.uniform(0.08, 0.22)
