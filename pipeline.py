"""一次完整答题流程：截屏 → AI → 移动鼠标 / 逐字输入。

运行在工作线程里，通过回调把状态抛给 UI；stop 事件可随时中断。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable

import pyautogui

import ai_client
import screen
import storage
from config import Config
from humanizer import Humanizer
from input_engine import Interrupted, move_mouse_smooth, type_text
from logging_setup import get_logger
from prompts import prompt_for

log = get_logger()

StatusCb = Callable[[str], None]


@dataclass
class RunResult:
    answer: ai_client.Answer | None = None
    typed: bool = False
    elapsed: float = 0.0
    error: str | None = None
    screenshot: screen.Screenshot | None = None


class Pipeline:
    def __init__(self, cfg: Config, on_status: StatusCb) -> None:
        self.cfg = cfg
        self.on_status = on_status
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._running = False

    # ------------------------------------------------------------ 控制

    def start_async(self) -> bool:
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._stop.clear()
        threading.Thread(target=self._run, daemon=True, name="pipeline").start()
        return True

    def stop(self) -> None:
        if self._running:
            log.info("收到中止请求")
            self._stop.set()

    @property
    def running(self) -> bool:
        return self._running

    # ------------------------------------------------------------ 主流程

    def _run(self) -> None:
        started = time.perf_counter()
        result = RunResult()
        try:
            self._execute(result)
        except Interrupted:
            result.error = "已中止"
            self.on_status("已中止")
        except ai_client.AIError as exc:
            result.error = str(exc)
            log.error("AI 调用失败：%s", exc)
            self.on_status(f"AI 失败：{exc}")
        except Exception as exc:  # 线程里的兜底，避免静默失败
            result.error = f"{type(exc).__name__}: {exc}"
            log.exception("答题流程异常")
            self.on_status(f"出错：{exc}")
        finally:
            result.elapsed = time.perf_counter() - started
            if result.answer is not None or result.error:
                try:
                    storage.record(
                        result.answer or ai_client.Answer(),
                        model=self.cfg.model,
                        typed=result.typed,
                        elapsed=result.elapsed,
                        error=result.error,
                    )
                except OSError:
                    log.exception("写入历史失败")
            self.on_status(f"完成（{result.elapsed:.1f}s）" if not result.error else result.error)
            with self._lock:
                self._running = False

    def _execute(self, result: RunResult) -> None:
        cfg = self.cfg
        if not cfg.api_key:
            raise ai_client.AIError("尚未配置 API Key")

        preset = cfg.speed_preset()
        hz = Humanizer(
            median_delay=preset["median"],
            sigma=preset["sigma"],
            typo_rate=cfg.typo_rate,
        )

        self.on_status("截图中…")
        shot = screen.capture(cfg.max_image_side)
        result.screenshot = shot
        self._check_stop()

        self.on_status("AI 解析中…")
        data_url = shot.to_data_url(cfg.jpeg_quality)
        answer = ai_client.ask(
            cfg.base_url,
            cfg.model,
            cfg.api_key,
            data_url,
            user_prompt=prompt_for(cfg.prompt_template, cfg.custom_prompt),
            timeout=cfg.request_timeout,
        )
        result.answer = answer
        self._check_stop()

        if answer.type == "unknown" or not (answer.answer or answer.target):
            self.on_status("未识别到题目或答案为空")
            return

        self.on_status(f"已得到答案：{answer.summary()}")
        time.sleep(hz.think_delay())
        self._check_stop()

        moved = False
        if (
            cfg.move_mouse
            and answer.target is not None
            and 0 <= answer.target[0] < shot.image.width
            and 0 <= answer.target[1] < shot.image.height
        ):
            sx = shot.origin_x + int(round(answer.target[0] * shot.scale))
            sy = shot.origin_y + int(round(answer.target[1] * shot.scale))
            # 落在选项上方一点，避免遮住文字
            sy -= 2
            log.info("鼠标移动到选项 (%d, %d)", sx, sy)
            self.on_status("移动到选项…")
            move_mouse_smooth(sx, sy, hz, self._stop)
            moved = True
            time.sleep(hz.approach_duration())
            self._check_stop()

        # 选择题且已悬停到选项上时，不再往输入框里补字母
        if moved and answer.type == "choice":
            return
        text = answer.answer
        if not text:
            return

        self.on_status(f"输入中（{len(text)} 字符）…")
        type_text(text, hz, self._stop)
        result.typed = True
        log.info("输入完成，共 %d 字符", len(text))

    def _check_stop(self) -> None:
        if self._stop.is_set():
            raise Interrupted()
        # 鼠标撞到屏幕左上角也能中止（pyautogui.FAILSAFE 已被关闭，这里手动实现）
        try:
            x, y = pyautogui.position()
            if x <= 0 and y <= 0:
                log.warning("检测到鼠标位于左上角，判定为中止")
                self._stop.set()
                raise Interrupted()
        except OSError:
            pass
